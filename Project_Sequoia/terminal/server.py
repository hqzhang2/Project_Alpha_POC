"""
Alpha Terminal Server - Updated for Ratio Analysis (Sprint 3)
"""
from http.server import HTTPServer, SimpleHTTPRequestHandler
import json
import math
import os
import socket
from urllib.parse import urlparse, parse_qs
import yfinance as yf
import datetime
import pandas as pd
import sys

# Add current dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from indicators import calculate_rsi, calculate_macd, calculate_bollinger_bands

def find_free_port(start=9090, max_attempts=10):
    for port in range(start, start + max_attempts):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(('', port))
            s.close()
            return port
        except OSError:
            continue
    return start

def clean_value(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if hasattr(v, 'isoformat'):
        return v.isoformat()
    return v

def clean_dict(d):
    if isinstance(d, dict):
        return {k: clean_dict(clean_value(v)) for k, v in d.items()}
    elif isinstance(d, list):
        return [clean_dict(clean_value(x)) for x in d]
    return clean_value(d)

class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        
        if path == '/api/ratio':
            t1 = qs.get('t1', ['XLE'])[0]
            t2 = qs.get('t2', ['SPY'])[0]
            tf = qs.get('tf', ['1Y'])[0]
            sma_p = int(qs.get('sma', [20])[0])
            
            return self.send_json(self.get_ratio_data(t1, t2, tf, sma_p))

        if path == '/api/quotes':
            from quotes import get_quotes
            return self.send_json(get_quotes(qs.get('tickers', ['SPY'])[0].split(',')))
        
        if path == '/api/options':
            from options import get_options_chain
            return self.send_json(get_options_chain(qs.get('ticker', ['SPY'])[0], qs.get('expiry', [None])[0]))
        
        if path == '/api/expirations':
            from options import get_expirations
            ticker = qs.get('ticker', ['SPY'])[0]
            expirations = get_expirations(ticker)
            standard = []
            for e in expirations:
                try:
                    dt = datetime.datetime.strptime(e, '%Y-%m-%d')
                    if dt.weekday() == 4 and 15 <= dt.day <= 21:
                        standard.append({'date': e, 'label': dt.strftime('%b %Y') + " (Std)"})
                except: continue
            return self.send_json({'ticker': ticker, 'expirations': expirations, 'standard': standard})

        if path == '/api/screen':
            from options import get_expirations, get_options_chain
            ticker = qs.get('ticker', ['SPY'])[0]
            expirations = get_expirations(ticker)
            all_hits = []
            for expiry in expirations[:8]:
                chain = get_options_chain(ticker, expiry)
                for opt_type in ['calls', 'puts']:
                    for row in chain.get(opt_type, []):
                        row['type'] = opt_type[:-1].upper()
                        row['expiry'] = expiry
                        all_hits.append(row)
            return self.send_json({'ticker': ticker, 'results': all_hits})
        
        return SimpleHTTPRequestHandler.do_GET(self)

    def get_ratio_data(self, t1, t2, tf, sma_period):
        period_map = {'1M': '1mo', '3M': '3mo', '6M': '6mo', 'YTD': 'ytd', '1Y': '1y', '5Y': '5y'}
        p = period_map.get(tf, '1y')
        
        d1 = yf.Ticker(t1).history(period=p)['Close']
        d2 = yf.Ticker(t2).history(period=p)['Close']
        
        # Align data
        df = pd.DataFrame({'t1': d1, 't2': d2}).dropna()
        df['ratio'] = df['t1'] / df['t2']
        
        # Indicators
        df['sma'] = df['ratio'].rolling(window=sma_period).mean()
        df['rsi'] = calculate_rsi(df['ratio'])
        macd, signal, hist = calculate_macd(df['ratio'])
        upper, lower = calculate_bollinger_bands(df['ratio'])
        
        return {
            'labels': [x.strftime('%Y-%m-%d') for x in df.index],
            'ratio': df['ratio'].tolist(),
            'sma': df['sma'].tolist(),
            'rsi': df['rsi'].tolist(),
            'macd': macd.tolist(),
            'macd_signal': signal.tolist(),
            'macd_hist': hist.tolist(),
            'upper': upper.tolist(),
            'lower': lower.tolist(),
            't1_name': t1,
            't2_name': t2
        }
    
    def send_json(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(clean_dict(data)).encode())

def run(port=9090):
    os.chdir(os.path.dirname(os.path.abspath(__file__)) or '.')
    server = HTTPServer(('', port), Handler)
    print(f'Alpha Terminal: http://localhost:{port}')
    server.serve_forever()

if __name__ == '__main__':
    run()
