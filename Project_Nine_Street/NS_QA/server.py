import os
import json
import socket
import pandas as pd
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import yfinance as yf
import sys

# Add parent dir to path so we can import our engines
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from nsae_features import NSAEFeatureEngineer
from nsoe_pricing import NSOEOptionEngine
from ns_backtester import NSBacktester
from ns_quant_models import PhDAlphaModels

PORT = int(os.environ.get('PORT', 9199))

class NSRequestHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        super().end_headers()

    def do_GET(self):
        parsed_path = urlparse(self.path)
        path = parsed_path.path
        qs = parse_qs(parsed_path.query)

        if path == '/' or path == '/index.html':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            with open(os.path.join(os.path.dirname(__file__), 'index.html'), 'rb') as f:
                self.wfile.write(f.read())
            return

        if path == '/api/chart':
            ticker = qs.get('ticker', ['SPY'])[0]
            try:
                # Fetch more data to ensure we have enough for HMM
                tkr = yf.Ticker(ticker)
                hist = tkr.history(period='1y')
                
                # Get features to compute regimes
                engine = NSAEFeatureEngineer(tickers=[ticker], start_date=(hist.index[0] - pd.Timedelta(days=100)).strftime('%Y-%m-%d'))
                engine.fetch_data()
                engine.generate_features()
                features = engine.calculate_continuous_signal()
                ticker_feats = features[features['Ticker'] == ticker].set_index('date')
                
                if not ticker_feats.empty:
                    regime = PhDAlphaModels.detect_market_regime(ticker_feats, n_regimes=3)
                    # Align to hist
                    regime = regime.reindex(hist.index).ffill().fillna(0)
                    regimes_list = regime.tolist()
                else:
                    regimes_list = [0] * len(hist)

                # Limit to 3mo for display to match original
                display_hist = hist.last('90D')
                display_regimes = regimes_list[-len(display_hist):]
                
                data = {
                    'dates': display_hist.index.strftime('%Y-%m-%d').tolist(),
                    'open': display_hist['Open'].tolist(),
                    'high': display_hist['High'].tolist(),
                    'low': display_hist['Low'].tolist(),
                    'close': display_hist['Close'].tolist(),
                    'volume': display_hist['Volume'].tolist(),
                    'regimes': display_regimes
                }
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
            return

        if path == '/api/nsae':
            try:
                universe = ["SPY", "QQQ", "XLK", "XLE", "XLV", "XLF", "XLI", "XLB", "XLY", "XLP", "XLU", "XLRE", "XLC", "EFA", "EEM", "AGG", "TLT", "IEI", "DBC", "GLD"]
                engine = NSAEFeatureEngineer(tickers=universe, start_date="2024-01-01")
                engine.fetch_data()
                engine.generate_features()
                signals = engine.calculate_continuous_signal()
                
                latest_date = signals['date'].max()
                latest_signals = signals[signals['date'] == latest_date].sort_values(by='nsae_signal', ascending=False)
                
                latest_signals['date'] = latest_signals['date'].dt.strftime('%Y-%m-%d')
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(latest_signals.to_json(orient='records').encode())
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
            return

        if path == '/api/nsoe':
            ticker = qs.get('ticker', ['SPY'])[0]
            try:
                engine = NSOEOptionEngine(ticker=ticker)
                chain = engine.fetch_options_chain(min_dte=30, max_dte=45)
                if not chain.empty:
                    engine.calculate_greeks()
                    targets = engine.screen_premium_targets(target_delta_call=0.30, target_delta_put=-0.30)
                    
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(targets.to_json(orient='records').encode())
                else:
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps([]).encode())
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
            return

        if path == '/api/backtest':
            try:
                universe = ["SPY", "QQQ", "XLK", "XLE", "XLV", "XLF", "XLI", "XLB", "XLY", "XLP", "XLU", "XLRE", "XLC", "EFA", "EEM", "AGG", "TLT", "IEI", "DBC", "GLD"]
                bt = NSBacktester(tickers=universe, start_date="2022-01-01")
                strategy = qs.get('strategy', ['hmm_regime'])[0]
                target_vol_str = qs.get('target_vol', ['15'])[0]
                target_vol = float(target_vol_str) / 100.0
                
                res = bt.run_backtest(strategy=strategy, target_vol=target_vol)
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(res).encode())
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
            return

        if path == '/api/portfolio':
            try:
                with open(os.path.join(os.path.dirname(__file__), '..', 'paper_portfolio.json'), 'r') as f:
                    data = json.load(f)
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
            return

        
        if path == '/api/live_feed':
            try:
                # Read portfolio to know what to fetch
                with open(os.path.join(os.path.dirname(__file__), '..', 'paper_portfolio.json'), 'r') as f:
                    port = json.load(f)
                
                tickers = list(port['positions'].get('equities', {}).keys())
                if not tickers:
                    tickers = ['SPY']
                
                # Fetch live data (1 minute interval for the last day)
                feed_data = []
                for ticker in tickers:
                    tkr = yf.Ticker(ticker)
                    live = tkr.history(period="1d", interval="1m")
                    if not live.empty:
                        last_price = float(live['Close'].iloc[-1])
                        prev_close = float(tkr.history(period="5d")['Close'].iloc[-2]) # approx prev close
                        pct_change = ((last_price / prev_close) - 1) * 100
                        feed_data.append({
                            'ticker': ticker,
                            'price': round(last_price, 2),
                            'change': round(pct_change, 2),
                            'timestamp': live.index[-1].strftime('%H:%M:%S EST')
                        })
                
                # We can also add simulated or recent system events
                events = []
                if 'history' in port and len(port['history']) > 0:
                    events.append({
                        'time': port['account']['last_updated'],
                        'message': f"Daily PnL Updated. NAV: ${port['account']['total_nav']}"
                    })
                events.append({
                    'time': feed_data[0]['timestamp'] if feed_data else 'Now',
                    'message': "Live Market Data Fetched"
                })

                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'prices': feed_data, 'events': events}).encode())
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
            return

        super().do_GET()

def run():
    server_address = ('0.0.0.0', PORT)
    httpd = HTTPServer(server_address, NSRequestHandler)
    print(f"Nine Street QA Server running on port {PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    httpd.server_close()

if __name__ == '__main__':
    run()
