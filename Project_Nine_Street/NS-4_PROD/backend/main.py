"""
NS-4: Ratio Trading System (PROD - stdlib)
===========================================
Clone of NS-2 but for trading ratios (e.g., DBC/SPY, TLT/IEF).
Identifies regime changes in ratio momentum and generates trading signals.

P7-A remediation: ported from FastAPI to the stdlib http.server pattern
(consistent with NS-2_PROD and all QA servers; FastAPI/pydantic_core is
broken on every interpreter on this machine). Business logic unchanged.
API surface preserved: /api/v1/ratios, /api/v1/ratio/{symbol}, /api/v1/all,
/api/v1/rankings, /api/v1/health.
"""
import warnings
warnings.filterwarnings("ignore")
import os
import json
import re
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import numpy as np
import pandas as pd
import yfinance as yf

PORT = int(os.environ.get("PORT", 9240))
dashboard_path = os.path.join(os.path.dirname(__file__), "ns4_dashboard.html")

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
def generate_signal(latest: pd.Series, prev: pd.Series = None) -> dict:
    """Generate trading signal based on regime and indicators"""
    score = 0
    reasons = []

    # RSI
    if latest.get('RSI', 50) < 30:
        score += 2
        reasons.append("RSI oversold")
    elif latest.get('RSI', 50) > 70:
        score -= 2
        reasons.append("RSI overbought")

    # MACD — compare current vs previous histogram
    cur_hist = latest.get('MACD_hist', 0)
    prev_hist = prev.get('MACD_hist', 0) if prev is not None else 0
    if cur_hist > 0 and cur_hist > prev_hist:
        score += 1
        reasons.append("MACD bullish")
    elif cur_hist < 0 and cur_hist < prev_hist:
        score -= 1
        reasons.append("MACD bearish")

    # ADX trend strength
    if latest.get('ADX', 0) > 25:
        score += 1 if latest.get('plus_di', 0) > latest.get('minus_di', 0) else -1
        reasons.append("Strong trend")

    # Bollinger position
    if latest.get('BB_position', 0.5) < 0.2:
        score += 1
        reasons.append("Near lower BB")
    elif latest.get('BB_position', 0.5) > 0.8:
        score -= 1
        reasons.append("Near upper BB")

    # Trend alignment
    if latest.get('Close', 0) > latest.get('SMA_50', 0) > latest.get('SMA_200', 0):
        score += 2
        reasons.append("Bullish alignment")
    elif latest.get('Close', 0) < latest.get('SMA_50', 0) < latest.get('SMA_200', 0):
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

# ── Handlers (FastAPI endpoints -> plain functions) ─────────────────────────

def get_ratio_analysis(symbol: str) -> dict:
    """Get comprehensive analysis for a ratio. Raises KeyError/ValueError on bad input."""
    ratio = next((r for r in RATIOS if r['symbol'] == symbol), None)
    if not ratio:
        raise ValueError("Ratio not found")

    num = yf.Ticker(ratio['num']).history(period='2y')
    denom = yf.Ticker(ratio['denom']).history(period='2y')

    if num.empty or denom.empty:
        raise ValueError("Cannot fetch ratio data")

    aligned = pd.DataFrame({'num': num['Close'], 'denom': denom['Close']}).dropna()
    ratio_series = aligned['num'] / aligned['denom']

    df = pd.DataFrame({'Close': ratio_series, 'High': ratio_series, 'Low': ratio_series})
    df['High'] = ratio_series.rolling(5).max()
    df['Low'] = ratio_series.rolling(5).min()
    df = calculate_features(df)

    regimes = detect_regime(df['Close'])
    df['regime'] = regimes

    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else latest

    signal_info = generate_signal(latest, prev)

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
        "sma": {  # P7-A fix: key was " SMA" (leading space) in the FastAPI original
            "short": round(latest.get('SMA_10', 0), 4),
            "medium": round(latest.get('SMA_50', 0), 4),
            "long": round(latest.get('SMA_200', 0), 4)
        }
    }


def get_all_ratios() -> dict:
    """Get signals for all ratios"""
    results = []
    for ratio in RATIOS:
        try:
            data = get_ratio_analysis(ratio['symbol'])
            results.append(data)
        except Exception as e:
            results.append({"symbol": ratio['symbol'], "error": str(e)})

    return {"ratios": results, "count": len(results)}


def get_rankings(sort_by: str = "score") -> list:
    """Get rankings of all ratios"""
    data = get_all_ratios()
    valid = [r for r in data['ratios'] if 'error' not in r]

    if sort_by == "change":
        valid.sort(key=lambda x: x.get('change_pct', 0), reverse=True)
    elif sort_by == "score":
        valid.sort(key=lambda x: x.get('score', 0), reverse=True)
    elif sort_by == "rsi":
        valid.sort(key=lambda x: x.get('indicators', {}).get('rsi', 50), reverse=True)  # P7-A fix: key was 'RSI'

    return valid


# ── HTTP layer ───────────────────────────────────────────────────────────────

class NS4Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    def _json(self, code, data):
        self.send_response(code)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def do_GET(self):
        parsed = urlparse(self.path)
        path, qs = parsed.path, parse_qs(parsed.query)

        if path in ('/', '/ns4_dashboard.html'):
            if os.path.exists(dashboard_path):
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                with open(dashboard_path, 'rb') as f:
                    self.wfile.write(f.read())
                return
            return self._json(200, {"service": "NS-4", "note": "dashboard not deployed"})

        if path in ('/health', '/api/v1/health'):
            return self._json(200, {"status": "ok", "service": "NS-4 Ratio Trading"})

        if path == '/api/v1/ratios':
            return self._json(200, {"ratios": RATIOS, "count": len(RATIOS)})

        if path == '/api/v1/all':
            return self._json(200, get_all_ratios())

        m = re.fullmatch(r'/api/v1/ratio/(.+)', path)  # symbol may contain '/' (GLD/SPY)
        if m:
            try:
                return self._json(200, get_ratio_analysis(m.group(1)))
            except ValueError as e:
                return self._json(404, {"error": str(e)})

        if path == '/api/v1/rankings':
            return self._json(200, get_rankings(qs.get('sort_by', ['score'])[0]))

        self.send_response(404)
        self.end_headers()


if __name__ == '__main__':
    os.chdir(os.path.dirname(__file__))
    server = HTTPServer(('0.0.0.0', PORT), NS4Handler)
    print(f"NS-4 PROD running on port {PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()
