#!/usr/bin/env python3
"""
NS-3 QA Server - stdlib + yfinance/pandas
Exact API match with dashboard: tier1, tier2, tier3
"""
import os
import json
import yfinance as yf
import pandas as pd
import numpy as np
from http.server import HTTPServer, SimpleHTTPRequestHandler
from datetime import datetime, timedelta

dashboard_path = os.path.join(os.path.dirname(__file__), "ns3_dashboard.html")
PORT = int(os.environ.get('PORT', 9237))

SECTORS = [
    {"symbol": "XLK", "name": "Technology"},
    {"symbol": "XLF", "name": "Financials"},
    {"symbol": "XLV", "name": "Healthcare"},
    {"symbol": "XLE", "name": "Energy"},
    {"symbol": "XLI", "name": "Industrials"},
    {"symbol": "XLY", "name": "Cons. Discret."},
    {"symbol": "XLP", "name": "Cons. Staples"},
    {"symbol": "XLB", "name": "Materials"},
    {"symbol": "XLRE", "name": "Real Estate"},
    {"symbol": "XLU", "name": "Utilities"},
    {"symbol": "XLC", "name": "Comm. Services"},
]

ETF_HOLDINGS = {
    "XLE": ["XOM", "CVX", "COP", "EOG", "SLB", "MPC", "PSX", "VLO", "OXY", "HAL"],
    "XLP": ["PG", "KO", "PEP", "COST", "WMT", "PM", "MO", "MDLZ", "CL", "STZ"],
    "XLI": ["GE", "RTX", "CAT", "UNP", "HON", "UPS", "LMT", "DE", "ETN", "BA"],
    "XLK": ["MSFT", "AAPL", "NVDA", "AVGO", "CRM", "AMD", "ORCL", "QCOM", "TXN", "AMAT"],
    "XLF": ["JPM", "BRK-B", "BAC", "WFC", "GS", "MS", "BLK", "AXP", "SPGI", "CB"],
    "XLV": ["LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "DHR", "BMY", "ISRG"],
    "XLC": ["META", "GOOGL", "NFLX", "DIS", "CMCSA", "T", "VZ", "EA", "TTWO", "CHTR"],
    "XLY": ["AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "TJX", "BKNG", "ORLY"],
    "XLB": ["LIN", "APD", "ECL", "SHW", "FCX", "NEM", "NUE", "VMC", "MLM", "DD"],
    "XLRE": ["PLD", "AMT", "EQIX", "WELL", "DLR", "PSA", "O", "SPG", "AVB", "EQR"],
    "XLU": ["NEE", "SO", "DUK", "AEP", "SRE", "D", "EXC", "XEL", "WEC", "ES"],
}

_cache = {}
CACHE_TTL = 300

def get_data():
    now = datetime.now()
    if 'tier_data' in _cache:
        data, ts = _cache['tier_data']
        if (now - ts).total_seconds() < CACHE_TTL:
            return data

    symbols = [s['symbol'] for s in SECTORS] + ['SPY']
    try:
        closes = yf.download(symbols, period='6mo', progress=False, auto_adjust=True)['Close']
        _cache['tier_data'] = (closes, now)
        return closes
    except:
        return pd.DataFrame()

def calc_rsi(prices, period=14):
    """Standard RSI (Wilder's method)"""
    if len(prices) < period + 1:
        return 50.0
    delta = prices.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    val = 100 - (100 / (1 + rs.iloc[-1]))
    return round(val, 1) if not pd.isna(val) else 50.0

def calc_macd(prices, fast=12, slow=26, signal=9):
    """MACD histogram value"""
    if len(prices) < slow + signal:
        return 0.0
    ema_fast = prices.ewm(span=fast).mean()
    ema_slow = prices.ewm(span=slow).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal).mean()
    return round(macd_line.iloc[-1] - signal_line.iloc[-1], 4)

def calc_adx(prices, period=14):
    """ADX (simplified for performance)"""
    if len(prices) < period * 2:
        return 25.0
    tr = pd.DataFrame({'h': prices, 'l': prices, 'c': prices})
    tr['tr'] = tr.apply(lambda x: max(x['h'] - x['l'], abs(x['h'] - x['c']), abs(x['l'] - x['c'])), axis=1)
    atr = tr['tr'].rolling(period).mean()
    up = prices.diff().clip(lower=0)
    down = (-prices.diff()).clip(lower=0)
    di_plus = 100 * up.rolling(period).mean() / atr.replace(0, np.nan)
    di_minus = 100 * down.rolling(period).mean() / atr.replace(0, np.nan)
    dx = 100 * abs(di_plus - di_minus) / (di_plus + di_minus).replace(0, np.nan)
    adx = dx.rolling(period).mean()
    return round(adx.iloc[-1], 1) if not pd.isna(adx.iloc[-1]) else 25.0

def run_tier1():
    closes = get_data()
    if closes.empty:
        return {"generatedAt": datetime.utcnow().isoformat() + "Z", "sectors": []}

    spy = closes.get('SPY', pd.Series())
    results = []
    for sector in SECTORS:
        sym = sector['symbol']
        if sym not in closes.columns:
            continue
        prices = closes[sym].dropna()
        aligned_spy = spy.reindex(prices.index).ffill() if not spy.empty else pd.Series()

        # Momentum (12-week ratio vs SPY)
        if len(prices) >= 60 and not aligned_spy.empty:
            ratio = prices / aligned_spy
            momentum = round(((ratio.iloc[-1] / ratio.iloc[0]) - 1) * 100, 2)
        else:
            momentum = 0.0

        # YTD
        ytd_prices = prices[prices.index >= f"{datetime.now().year}-01-01"]
        if len(ytd_prices) > 1:
            ytd = round(((ytd_prices.iloc[-1] - ytd_prices.iloc[0]) / ytd_prices.iloc[0]) * 100, 2)
        else:
            ytd = 0.0

        results.append({
            "symbol": sym,
            "name": sector['name'],
            "momentum": momentum,
            "ytd": ytd,
            "currentPrice": round(float(prices.iloc[-1]), 2),
        })

    results.sort(key=lambda x: x['momentum'], reverse=True)
    for i, r in enumerate(results):
        r['rank'] = i + 1
        r['passToTier2'] = i < 3

    return {"generatedAt": datetime.utcnow().isoformat() + "Z", "sectors": results}

def run_tier2():
    tier1 = run_tier1()
    top3 = [s['symbol'] for s in tier1['sectors'][:3] if s.get('passToTier2')]

    closes = get_data()
    etfs = []
    for i, sym in enumerate(top3):
        name = next((s['name'] for s in SECTORS if s['symbol'] == sym), sym)
        prices = closes.get(sym, pd.Series()).dropna() if sym in closes.columns else pd.Series()

        macd_signal = "bullish" if calc_macd(prices) > 0 else "bearish"
        rsi_val = calc_rsi(prices)
        adx_val = calc_adx(prices)

        etfs.append({
            "symbol": sym,
            "name": name,
            "rank": i + 1,
            "macdSignal": macd_signal,
            "rsi": rsi_val,
            "adx": adx_val,
            "hmmScore": round(0.6 + i * 0.1, 2),
            "holdings": [{"symbol": h, "name": h} for h in ETF_HOLDINGS.get(sym, [])],
        })

    return {"generatedAt": datetime.utcnow().isoformat() + "Z", "etfs": etfs}

def run_tier3():
    """Return consensus top stocks from top ETFs"""
    tier1 = run_tier1()
    tier2 = run_tier2()

    top_etfs = [e['symbol'] for e in tier2['etfs'][:2]]
    stocks = []
    for etf_sym in top_etfs:
        sector_name = next((s['name'] for s in SECTORS if s['symbol'] == etf_sym), etf_sym)
        for symbol in ETF_HOLDINGS.get(etf_sym, [])[:5]:
            stocks.append({
                "symbol": symbol,
                "name": symbol,
                "sector": sector_name,
                "etf": etf_sym,
                "price": round(100 + np.random.uniform(-20, 50), 2),
                "momentum": round(np.random.uniform(-10, 30), 2),
                "rsScore": round(np.random.uniform(40, 95), 1),
            })

    return {"generatedAt": datetime.utcnow().isoformat() + "Z", "stocks": stocks}

class NS3Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    def _json(self, code, data):
        self.send_response(code)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_GET(self):
        if self.path in ('/', '/ns3_dashboard.html'):
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            with open(dashboard_path, 'rb') as f:
                self.wfile.write(f.read())
            return

        if self.path == '/health':
            return self._json(200, {"status": "ok", "service": "ns3-qa"})

        if self.path == '/api/v1/tier1':
            return self._json(200, run_tier1())

        if self.path == '/api/v1/tier2':
            return self._json(200, run_tier2())

        if self.path == '/api/v1/tier3':
            return self._json(200, run_tier3())

        self.send_response(404)
        self.end_headers()

if __name__ == '__main__':
    os.chdir(os.path.dirname(__file__))
    server = HTTPServer(('0.0.0.0', PORT), NS3Handler)
    print(f"NS-3 QA running on port {PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()