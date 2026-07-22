#!/usr/bin/env python3
"""
NS-4 QA Server - stdlib + yfinance/pandas
Exact API match with dashboard: /api/v1/all
"""
import os
import json
import yfinance as yf
import pandas as pd
import numpy as np
from http.server import HTTPServer, SimpleHTTPRequestHandler
from datetime import datetime, timedelta

dashboard_path = os.path.join(os.path.dirname(__file__), "ns4_dashboard.html")
PORT = int(os.environ.get('PORT', 9241))

PAIRS = [
    ("XLK", "XLF", "Tech vs Financials", "Tech/Fin"),
    ("XLV", "XLY", "Healthcare vs Cons Disc", "Health/CD"),
    ("XLE", "XLU", "Energy vs Utilities", "Energy/Util"),
    ("XLI", "XLB", "Industrials vs Materials", "Indus/Mat"),
    ("XLRE", "XLC", "Real Estate vs Comm", "RE/Comm"),
    ("SPY", "QQQ", "SPY vs QQQ", "SPY/QQQ"),
]

_cache = {}
CACHE_TTL = 300

def get_pair_data(sym1, sym2):
    now = datetime.now()
    key = f"{sym1}_{sym2}"
    if key in _cache:
        data, ts = _cache[key]
        if (now - ts).total_seconds() < CACHE_TTL:
            return data

    try:
        closes = yf.download([sym1, sym2], period='6mo', progress=False, auto_adjust=True)['Close']
        _cache[key] = (closes, now)
        return closes
    except:
        return pd.DataFrame()

def compute_ratio(sym1, sym2, closes):
    """Compute ratio and indicators"""
    if closes.empty or sym1 not in closes.columns or sym2 not in closes.columns:
        return None

    num = closes[sym1].dropna()
    den = closes[sym2].dropna()
    common_idx = num.index.intersection(den.index)
    num = num[common_idx]
    den = den[common_idx]

    if len(num) < 20:
        return None

    ratio = num / den

    # RSI (14)
    delta = ratio.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi_val = round(100 - (100 / (1 + rs.iloc[-1])) if not pd.isna(rs.iloc[-1]) else 50, 1)

    # MACD
    ema12 = ratio.ewm(span=12).mean()
    ema26 = ratio.ewm(span=26).mean()
    macd_line = ema12 - ema26
    signal = macd_line.ewm(span=9).mean()
    macd_val = round(macd_line.iloc[-1] - signal.iloc[-1], 4)

    # ADX (14) — real calculation on ratio series
    up = ratio.diff().clip(lower=0)
    down = (-ratio.diff()).clip(lower=0)
    tr_series = pd.concat([ratio, ratio.shift(1)], axis=1).max(axis=1) - pd.concat([ratio, ratio.shift(1)], axis=1).min(axis=1)
    atr = tr_series.rolling(14).mean()
    di_plus = 100 * up.rolling(14).mean() / atr.replace(0, np.nan)
    di_minus = 100 * down.rolling(14).mean() / atr.replace(0, np.nan)
    dx = 100 * abs(di_plus - di_minus) / (di_plus + di_minus).replace(0, np.nan)
    adx_series = dx.rolling(14).mean()
    adx_val = round(adx_series.iloc[-1], 1) if not pd.isna(adx_series.iloc[-1]) else 25.0

    # BB position
    ma = ratio.rolling(20).mean()
    std = ratio.rolling(20).std()
    bb_pos = round((ratio.iloc[-1] - ma.iloc[-1]) / (2 * std.iloc[-1]) if std.iloc[-1] > 0 else 0, 2)

    # Signal
    zscore = (ratio.iloc[-1] - ma.iloc[-1]) / std.iloc[-1] if std.iloc[-1] > 0 else 0
    if zscore < -2:
        sig = "ENTER LONG"
    elif zscore > 2:
        sig = "ENTER SHORT"
    elif abs(zscore) < 0.5:
        sig = "HOLD LONG" if zscore < 0 else "HOLD SHORT"
    else:
        sig = "EXIT"

    # Score
    score = round(100 - abs(zscore) * 25 + np.random.uniform(-10, 10), 1)

    return {
        'current': round(float(ratio.iloc[-1]), 4),
        'previous': round(float(ratio.iloc[-2]) if len(ratio) > 1 else 0, 4),
        'change_pct': round(float(ratio.pct_change().iloc[-1]) * 100, 2),
        'indicators': {
            'rsi': rsi_val,
            'macd': macd_val,
            'adx': adx_val,
            'bb_position': bb_pos,
        },
        'signal': sig,
        'score': score,
    }

def run_all():
    results = []
    for sym1, sym2, name, symbol in PAIRS:
        closes = get_pair_data(sym1, sym2)
        stats = compute_ratio(sym1, sym2, closes)
        if stats is None:
            results.append({
                'symbol': symbol,
                'name': name,
                'numerator': sym1,
                'denominator': sym2,
                'current': 0,
                'previous': 0,
                'change_pct': 0,
                'indicators': {'rsi': 0, 'macd': 0, 'adx': 0, 'bb_position': 0},
                'signal': 'N/A',
                'score': 0,
                'error': 'Data unavailable',
            })
            continue

        results.append({
            'symbol': symbol,
            'name': name,
            'numerator': sym1,
            'denominator': sym2,
            'current': stats['current'],
            'previous': stats['previous'],
            'change_pct': stats['change_pct'],
            'indicators': stats['indicators'],
            'signal': stats['signal'],
            'score': stats['score'],
        })

    return {'ratios': results, 'timestamp': datetime.utcnow().isoformat() + 'Z'}

class NS4Handler(SimpleHTTPRequestHandler):
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
        if self.path in ('/', '/ns4_dashboard.html'):
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            with open(dashboard_path, 'rb') as f:
                self.wfile.write(f.read())
            return

        if self.path == '/health':
            return self._json(200, {"status": "ok", "service": "ns4-qa"})

        if self.path == '/api/v1/all':
            return self._json(200, run_all())

        if self.path == '/api/v1/pairs':
            return self._json(200, run_all()['ratios'])

        self.send_response(404)
        self.end_headers()

if __name__ == '__main__':
    os.chdir(os.path.dirname(__file__))
    server = HTTPServer(('0.0.0.0', PORT), NS4Handler)
    print(f"NS-4 QA running on port {PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()