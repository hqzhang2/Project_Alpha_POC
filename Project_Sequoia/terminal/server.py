"""
Alpha Terminal Server
Robust HTTP server with API endpoints
"""
from http.server import HTTPServer, SimpleHTTPRequestHandler
import json
import math
import os
import socket
from urllib.parse import urlparse, parse_qs
import yfinance
import datetime

# Add current dir to path
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
        
        if path == '/api/quotes':
            from quotes import get_quotes
            return self.send_json(get_quotes(qs.get('tickers', ['SPY'])[0].split(',')))
        
        if path == '/api/options':
            from options import get_options_chain
            ticker = qs.get('ticker', ['SPY'])[0]
            expiry = qs.get('expiry', [None])[0]
            return self.send_json(get_options_chain(ticker, expiry))
        
        if path == '/api/expirations':
            from options import get_expirations
            ticker = qs.get('ticker', ['SPY'])[0]
            expirations = get_expirations(ticker)
            
            # Filter standard options (3rd Friday)
            standard_expiries = []
            for e in expirations:
                dt = datetime.datetime.strptime(e, '%Y-%m-%d')
                # 3rd Friday is between 15th and 21st
                if dt.weekday() == 4 and 15 <= dt.day <= 21:
                    standard_expiries.append({'date': e, 'label': dt.strftime('%b %Y') + " (Std)"})
            
            return self.send_json({'ticker': ticker, 'expirations': expirations, 'standard': standard_expiries})

        if path == '/api/chart':
            ticker = qs.get('ticker', ['SPY'])[0]
            tf = qs.get('tf', ['1M'])[0]
            return self.send_json(get_chart(ticker, tf))
        
        return SimpleHTTPRequestHandler.do_GET(self)
    
    def send_json(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(clean_dict(data)).encode())

def get_chart(ticker, tf):
    period_map = {'1D': '1d', '1W': '5d', '1M': '1mo', '3M': '3mo', '1Y': '1y', 'YTD': 'ytd', '5Y': '5y'}
    try:
        hist = yfinance.Ticker(ticker).history(period=period_map.get(tf, '1mo'), interval='1d')
        return {
            'labels': [x.strftime('%m/%d') for x in hist.index],
            'prices': [clean_value(float(x)) for x in hist['Close']],
            'volumes': [clean_value(int(x)) for x in hist['Volume']]
        }
    except Exception as e:
        return {'error': str(e)}

def run(port=9090):
    os.chdir(os.path.dirname(os.path.abspath(__file__)) or '.')
    server = HTTPServer(('', port), Handler)
    print(f'Alpha Terminal: http://localhost:{port}')
    server.serve_forever()

if __name__ == '__main__':
    run()
