#!/usr/bin/env python3
"""
NS-1 Capital Preservation Server (QA)
Multi-factor VIX-aware ETF rotation.
Endpoints: signals, vix, performance, chart, health, portfolio, live_feed, backtest_curve.
"""

import os
import sys
import json
import time
import warnings
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")

PORT = int(os.environ.get('PORT', 9219))
CACHE_TTL = 300
DASHBOARD_PATH = os.path.join(os.path.dirname(__file__), 'index.html')
SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts'))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

ENGINES_AVAILABLE = False
try:
    from ns_capital_preservation import (
        FeatureEngineer as _FeatEng,
        UNIVERSE, CRISIS_VIX_IN, CRISIS_SAFE,
        vix_exposure_cap, compute_composite_scores,
        simulate_portfolio, run_capital_preservation_backtest, INITIAL_CAPITAL,
    )
    FeatureEngineer = _FeatEng
    ENGINES_AVAILABLE = True
except ImportError as e:
    print(f"NS-1: Engine import unavailable — {e}")

_cache = {}

def cached(key, ttl=CACHE_TTL):
    now = datetime.now()
    if key in _cache:
        data, ts = _cache[key]
        if (now - ts).total_seconds() < ttl:
            return data
    return None

def cache_set(key, data):
    _cache[key] = (data, datetime.now())


def get_engine():
    eng = cached('engine')
    if eng is not None:
        return eng
    if not ENGINES_AVAILABLE:
        return None
    eng = FeatureEngineer(tickers=list(UNIVERSE), start_date='2024-01-01')
    eng.fetch_all()
    eng.compute_features()
    cache_set('engine', eng)
    return eng


def get_current_signals():
    eng = get_engine()
    if eng is None:
        return None
    import pandas as pd
    import numpy as np
    daily_idx = eng.prices.index
    scores = compute_composite_scores(eng.features, eng.prices, eng.returns, eng.spy_vol, daily_idx)
    latest = scores.iloc[-1].sort_values(ascending=False)
    results = []
    for ticker, score in latest.items():
        price = float(eng.prices[ticker].iloc[-1]) if ticker in eng.prices.columns else 0
        feat = eng.features.get(ticker)
        rsi_val = float(feat['RSI'].iloc[-1]) if feat is not None and 'RSI' in feat.columns else 50
        adx_val = float(feat['ADX'].iloc[-1]) if feat is not None and 'ADX' in feat.columns else 20
        vol_val = float(feat['realized_vol'].iloc[-1]) if feat is not None and 'realized_vol' in feat.columns else 0.15
        ret_63 = float(feat['ret_1d'].rolling(63).sum().iloc[-1]) if feat is not None and 'ret_1d' in feat.columns else 0
        results.append({
            'ticker': ticker, 'score': round(float(score), 3), 'price': round(price, 2),
            'rsi': round(rsi_val, 1), 'adx': round(adx_val, 1),
            'vol_annual': round(vol_val * 100, 1), 'ret_63d_pct': round(ret_63 * 100, 1),
        })
    return results


def get_vix_status():
    eng = get_engine()
    if eng is None:
        return None
    import numpy as np
    vix_series = eng.vix_data
    if vix_series is None or vix_series.empty:
        return {'vix': 0, 'exposure_cap': 1.0, 'crisis_mode': False, 'regime': 'UNKNOWN'}
    current_vix = float(vix_series.iloc[-1])
    cap = vix_exposure_cap(current_vix)
    crisis = current_vix >= CRISIS_VIX_IN
    if current_vix < 15: regime = 'LOW_VOL'
    elif current_vix < 20: regime = 'NORMAL'
    elif current_vix < 25: regime = 'ELEVATED'
    elif current_vix < 30: regime = 'FEAR'
    elif current_vix < 40: regime = 'PANIC'
    else: regime = 'EXTREME'
    vix_5d_ago = float(vix_series.iloc[-6]) if len(vix_series) > 5 else current_vix
    return {
        'vix': round(current_vix, 1), 'vix_change_5d': round(current_vix - vix_5d_ago, 1),
        'exposure_cap': round(cap, 2), 'exposure_cap_pct': round(cap * 100, 0),
        'crisis_mode': crisis, 'crisis_threshold': CRISIS_VIX_IN,
        'regime': regime, 'safe_havens': list(CRISIS_SAFE) if crisis else None,
        'min_cash': 0.35 if current_vix > 35 else (0.25 if current_vix > 30 else (0.15 if current_vix > 25 else 0.05)),
    }


PERFORMANCE_SNAPSHOT = {
    'strategy': {'cagr_pct': 4.5, 'max_dd_pct': -21.7, 'sharpe': 0.09, 'sortino': 0.11, 'win_rate_pct': 53.2, 'positive_months_pct': 59.8, 'worst_month_pct': -6.7},
    'benchmark': {'cagr_pct': 14.0, 'max_dd_pct': -33.7, 'sharpe': 0.62},
    'period': '2010-01 – 2026-07 (16.5 yr)',
    'trades': '~755/yr, monthly rebalance, top-3 of 22',
    'vix40_return': 0.4,
}


class NS1Handler(SimpleHTTPRequestHandler):
    def _json(self, code, data):
        import math
        def sanitize(obj):
            if isinstance(obj, float) and math.isnan(obj): return None
            if isinstance(obj, dict): return {k: sanitize(v) for k, v in obj.items()}
            if isinstance(obj, list): return [sanitize(v) for v in obj]
            return obj
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(sanitize(data)).encode())

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        super().end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path in ('/', '/index.html'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            with open(DASHBOARD_PATH, 'rb') as f:
                self.wfile.write(f.read())
            return

        if path == '/health':
            vix = get_vix_status()
            return self._json(200, {'status': 'ok', 'service': 'ns1-capital-preservation', 'engines_available': ENGINES_AVAILABLE, 'vix': vix, 'port': PORT})

        if path == '/api/signals':
            if not ENGINES_AVAILABLE: return self._json(503, {'error': 'Engine unavailable'})
            try:
                signals = get_current_signals()
                return self._json(200, {'signals': signals, 'strategy': 'capital_preservation'})
            except Exception as e:
                return self._json(500, {'error': str(e)})

        if path == '/api/vix':
            if not ENGINES_AVAILABLE: return self._json(503, {'error': 'Engine unavailable'})
            try:
                return self._json(200, get_vix_status())
            except Exception as e:
                return self._json(500, {'error': str(e)})

        if path == '/api/performance':
            return self._json(200, PERFORMANCE_SNAPSHOT)

        if path == '/api/chart':
            if not ENGINES_AVAILABLE: return self._json(503, {'error': 'Engine unavailable'})
            ticker = qs.get('ticker', ['SPY'])[0]
            try:
                eng = get_engine()
                if eng is None: return self._json(503, {'error': 'Engine unavailable'})
                import numpy as np
                if ticker not in eng.prices.columns:
                    return self._json(404, {'error': f'Ticker {ticker} not found'})
                display = eng.prices[ticker].dropna().last('365D')
                display_vix = eng.vix_data.reindex(display.index).ffill()
                return self._json(200, {
                    'ticker': ticker,
                    'dates': display.index.strftime('%Y-%m-%d').tolist(),
                    'close': display.round(2).tolist(),
                    'vix': display_vix.round(1).tolist(),
                    'vix_ma20': display_vix.rolling(20).mean().round(1).tolist(),
                })
            except Exception as e:
                return self._json(500, {'error': str(e)})

        if path == '/api/backtest_curve':
            bt = cached('backtest_curve', ttl=86400)
            if bt is None:
                try:
                    from ns_capital_preservation import run_capital_preservation_backtest
                    m, nav, vix, spy, trades, weights = run_capital_preservation_backtest()
                    nav_norm = nav / nav.iloc[0] * 100
                    spy_norm = spy / spy.iloc[0] * 100
                    bt = {'dates': nav.index.strftime('%Y-%m-%d').tolist(), 'strategy': nav_norm.round(2).tolist(), 'benchmark': spy_norm.round(2).tolist()}
                    cache_set('backtest_curve', bt)
                except Exception as e:
                    return self._json(500, {'error': f'Backtest failed: {e}'})
            return self._json(200, bt)

        if path == '/api/portfolio':
            try:
                portfolio_path = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'paper_portfolio.json')
                with open(portfolio_path, 'r') as f:
                    return self._json(200, json.load(f))
            except Exception as e:
                return self._json(500, {'error': str(e)})

        if path == '/api/live_feed':
            try:
                import yfinance as yf
                portfolio_path = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'paper_portfolio.json')
                with open(portfolio_path, 'r') as f:
                    port = json.load(f)
                tickers = list(port['positions'].get('equities', {}).keys()) or ['SPY']
                feed_data = []
                for ticker in tickers:
                    tkr = yf.Ticker(ticker)
                    live = tkr.history(period='1d', interval='1m')
                    if not live.empty:
                        last_price = float(live['Close'].iloc[-1])
                        prev_close = float(tkr.history(period='5d')['Close'].iloc[-2])
                        pct_change = ((last_price / prev_close) - 1) * 100
                        feed_data.append({'ticker': ticker, 'price': round(last_price, 2), 'change': round(pct_change, 2), 'timestamp': live.index[-1].strftime('%H:%M:%S EST')})
                return self._json(200, {'prices': feed_data, 'events': [{'time': datetime.now().strftime('%H:%M:%S'), 'message': 'Live feed updated'}]})
            except Exception as e:
                return self._json(500, {'error': str(e)})

        super().do_GET()


def run():
    server_address = ('0.0.0.0', PORT)
    httpd = HTTPServer(server_address, NS1Handler)
    print(f"NS-1 Capital Preservation Server on port {PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    httpd.server_close()


if __name__ == '__main__':
    run()
