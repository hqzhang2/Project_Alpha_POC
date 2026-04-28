"""
NS-4: Ratio Trading System
===========================
Clone of NS-2 but for trading ratios (e.g., DBC/SPY, TLT/IEF).
Identifies regime changes in ratio momentum and generates trading signals.
"""
import warnings
warnings.filterwarnings("ignore")
import os
import numpy as np
import pandas as pd
import yfinance as yf
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any
import uvicorn

app = FastAPI(title="NS-4 Ratio Trading", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Ratio Universe ─────────────────────────────────────────────────────────────
RATIOS = [
    {"symbol": "DBC/SPY", "num": "DBC", "denom": "SPY", "name": "Commodities vs SPY"},
    {"symbol": "TLT/IEF", "num": "TLT", "denom": "IEF", "name": "Long vs Int Treasury"},
    {"symbol": "EEM/SPY", "num": "EEM", "denom": "SPY", "name": "EM vs SPY"},
    {"symbol": "GLD/SPY", "num": "GLD", "denom": "SPY", "name": "Gold vs SPY"},
    {"symbol": "VNQ/SPY", "num": "VNQ", "denom": "SPY", "name": "Real Estate vs SPY"},
    {"symbol": "KWEB/SPY", "num": "KWEB", "denom": "SPY", "name": "China vs SPY"},
    {"symbol": "EWJ/SPY", "num": "EWJ", "denom": "SPY", "name": "Japan vs SPY"},
    {"symbol": "EWG/SPY", "num": "EWG", "denom": "SPY", "name": "Germany vs SPY"},
    {"symbol": "XLB/SPY", "num": "XLB", "denom": "SPY", "name": "Materials vs SPY"},
    {"symbol": "XLE/SPY", "num": "XLE", "denom": "SPY", "name": "Energy vs SPY"},
    {"symbol": "XLF/SPY", "num": "XLF", "denom": "SPY", "name": "Financials vs SPY"},
    {"symbol": "XLK/SPY", "num": "XLK", "denom": "SPY", "name": "Tech vs SPY"},
    {"symbol": "XLV/SPY", "num": "XLV", "denom": "SPY", "name": "Healthcare vs SPY"},
    {"symbol": "XLY/SPY", "num": "XLY", "denom": "SPY", "name": "Cons. Disc vs SPY"},
    {"symbol": "XLP/SPY", "num": "XLP", "denom": "SPY", "name": "Cons. Staples vs SPY"},
]

# ── Feature Engineering ─────────────────────────────────────────────────────
def calculate_features(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate ratio-specific features"""
    df['returns'] = df['Close'].pct_change()
    df['log_returns'] = np.log(df['Close'] / df['Close'].shift(1))
    
    # Moving averages
    for window in [10, 20, 50, 100, 200]:
        df[f'SMA_{window}'] = df['Close'].rolling(window).mean()
        df[f'EMA_{window}'] = df['Close'].ewm(span=window).mean()
    
    # RSI
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    # MACD
    ema12 = df['Close'].ewm(span=12).mean()
    ema26 = df['Close'].ewm(span=26).mean()
    df['MACD'] = ema12 - ema26
    df['MACD_signal'] = df['MACD'].ewm(span=9).mean()
    df['MACD_hist'] = df['MACD'] - df['MACD_signal']
    
    # Bollinger Bands
    df['BB_middle'] = df['Close'].rolling(20).mean()
    bb_std = df['Close'].rolling(20).std()
    df['BB_upper'] = df['BB_middle'] + (bb_std * 2)
    df['BB_lower'] = df['BB_middle'] - (bb_std * 2)
    df['BB_position'] = (df['Close'] - df['BB_lower']) / (df['BB_upper'] - df['BB_lower'])
    
    # ATR
    high_low = df['High'] - df['Low']
    high_close = np.abs(df['High'] - df['Close'].shift())
    low_close = np.abs(df['Low'] - df['Close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    df['ATR'] = true_range.rolling(14).mean()
    df['ATR_percent'] = df['ATR'] / df['Close'] * 100
    
    # ADX
    plus_dm = df['High'].diff()
    minus_dm = -df['Low'].diff()
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm < 0] = 0
    tr14 = df['ATR'] * 14
    plus_di = 100 * (plus_dm.rolling(14).sum() / tr14)
    minus_di = 100 * (minus_dm.rolling(14).sum() / tr14)
    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
    df['ADX'] = dx.rolling(14).mean()
    df['plus_di'] = plus_di
    df['minus_di'] = minus_di
    
    return df

# ── Regime Detection ───────────────────────────────────────────────────────
def detect_regime(series: pd.Series, n_regimes: int = 3) -> np.ndarray:
    """Simple regime detection based on trend strength"""
    # Use ADX and RSI combination to identify regimes
    if len(series) < 50:
        return np.zeros(len(series), dtype=int)
    
    # Normalize
    normalized = (series - series.rolling(50).mean()) / series.rolling(50).std()
    
    # Simple 3-regime classifier based on normalized value
    regimes = np.zeros(len(series), dtype=int)
    regimes[normalized > 0.5] = 2  # Uptrend
    regimes[normalized < -0.5] = 1  # Downtrend
    
    return regimes

# ── Signal Generation ───────────────────────────────────────────────────────
def generate_signal(row: pd.Series) -> Dict[str, Any]:
    """Generate trading signal based on regime and indicators"""
    score = 0
    reasons = []
    
    # RSI
    if row.get('RSI', 50) < 30:
        score += 2
        reasons.append("RSI oversold")
    elif row.get('RSI', 50) > 70:
        score -= 2
        reasons.append("RSI overbought")
    
    # MACD
    if row.get('MACD_hist', 0) > 0 and row.get('MACD_hist', 0) > row.get('MACD_hist', 0):
        score += 1
        reasons.append("MACD bullish")
    elif row.get('MACD_hist', 0) < 0:
        score -= 1
        reasons.append("MACD bearish")
    
    # ADX trend strength
    if row.get('ADX', 0) > 25:
        score += 1 if row.get('plus_di', 0) > row.get('minus_di', 0) else -1
        reasons.append("Strong trend")
    
    # Bollinger position
    if row.get('BB_position', 0.5) < 0.2:
        score += 1
        reasons.append("Near lower BB")
    elif row.get('BB_position', 0.5) > 0.8:
        score -= 1
        reasons.append("Near upper BB")
    
    # Trend alignment
    if row.get('Close', 0) > row.get('SMA_50', 0) > row.get('SMA_200', 0):
        score += 2
        reasons.append("Bullish alignment")
    elif row.get('Close', 0) < row.get('SMA_50', 0) < row.get('SMA_200', 0):
        score -= 2
        reasons.append("Bearish alignment")
    
    if score >= 3:
        signal = "ENTER LONG"
    elif score <= -3:
        signal = "ENTER SHORT"
    elif score >= 1:
        signal = "HOLD LONG"
    elif score <= -1:
        signal = "HOLD SHORT"
    else:
        signal = "NEUTRAL"
    
    return {"signal": signal, "score": score, "reasons": reasons}

# ── API Endpoints ──────────────────────────────────────────────────────────
@app.get("/api/v1/ratios")
def get_ratios() -> Dict[str, Any]:
    """Return list of available ratios"""
    return {"ratios": RATIOS, "count": len(RATIOS)}

@app.get("/api/v1/ratio/{symbol}")
def get_ratio_analysis(symbol: str) -> Dict[str, Any]:
    """Get comprehensive analysis for a ratio"""
    # Find ratio
    ratio = next((r for r in RATIOS if r['symbol'] == symbol), None)
    if not ratio:
        raise HTTPException(status_code=404, detail="Ratio not found")
    
    # Fetch data
    num = yf.Ticker(ratio['num']).history(period='2y')
    denom = yf.Ticker(ratio['denom']).history(period='2y')
    
    if num.empty or denom.empty:
        raise HTTPException(status_code=400, detail="Cannot fetch ratio data")
    
    # Calculate ratio
    aligned = pd.DataFrame({'num': num['Close'], 'denom': denom['Close']}).dropna()
    ratio_series = aligned['num'] / aligned['denom']
    
    # Features
    df = pd.DataFrame({'Close': ratio_series, 'High': aligned['num']/aligned['denom'], 'Low': aligned['num']/aligned['denom']})
    df['High'] = ratio_series.rolling(5).max()
    df['Low'] = ratio_series.rolling(5).min()
    df = calculate_features(df)
    
    # Regimes
    regimes = detect_regime(df['Close'])
    df['regime'] = regimes
    
    # Latest values
    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else latest
    
    signal_info = generate_signal(latest)
    
    return {
        "symbol": symbol,
        "name": ratio['name'],
        "numerator": ratio['num'],
        "denominator": ratio['denom'],
        "current": round(latest['Close'], 4),
        "previous": round(prev['Close'], 4),
        "change_pct": round((latest['Close'] - prev['Close']) / prev['Close'] * 100, 2),
        "regime": int(latest.get('regime', 0)),
        "indicators": {
            "rsi": round(latest.get('RSI', 50), 1),
            "macd": round(latest.get('MACD', 0), 4),
            "macd_hist": round(latest.get('MACD_hist', 0), 4),
            "adx": round(latest.get('ADX', 0), 1),
            "bb_position": round(latest.get('BB_position', 0.5), 2),
            "atr_pct": round(latest.get('ATR_percent', 0), 2)
        },
        "signal": signal_info["signal"],
        "score": signal_info["score"],
        "reasons": signal_info["reasons"],
        " SMA": {
            "short": round(latest.get('SMA_10', 0), 4),
            "medium": round(latest.get('SMA_50', 0), 4),
            "long": round(latest.get('SMA_200', 0), 4)
        }
    }

@app.get("/api/v1/all")
def get_all_ratios() -> Dict[str, Any]:
    """Get signals for all ratios"""
    results = []
    for ratio in RATIOS:
        try:
            data = get_ratio_analysis(ratio['symbol'])
            results.append(data)
        except Exception as e:
            results.append({"symbol": ratio['symbol'], "error": str(e)})
    
    return {"ratios": results, "count": len(results)}

@app.get("/api/v1/rankings")
def get_rankings(sort_by: str = "score") -> List[Dict]:
    """Get rankings of all ratios"""
    data = get_all_ratios()
    valid = [r for r in data['ratios'] if 'error' not in r]
    
    if sort_by == "change":
        valid.sort(key=lambda x: x.get('change_pct', 0), reverse=True)
    elif sort_by == "score":
        valid.sort(key=lambda x: x.get('score', 0), reverse=True)
    elif sort_by == "rsi":
        valid.sort(key=lambda x: x.get('indicators', {}).get('RSI', 50), reverse=True)
    
    return valid

@app.get("/api/v1/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "service": "NS-4 Ratio Trading"}

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 9210))
    uvicorn.run(app, host="0.0.0.0", port=PORT)