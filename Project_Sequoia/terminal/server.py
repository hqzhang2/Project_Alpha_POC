"""
Alpha Terminal Server - Version 1.3.0
Refactored for maintainability and testability.
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
import numpy as np
from decimal import Decimal

# Add current dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import config
try:
    import config
    from indicators import calculate_rsi, calculate_macd, calculate_bollinger_bands
    from options import SafeJSONEncoder
except ImportError:
    class ConfigMock:
        DEFAULT_PORT = 9098
        HOST = 'localhost'
        TIMEFRAME_MAP = {'1D': '5d', '1W': '1mo', '1M': '3mo', '3M': '6mo', 'YTD': 'ytd', '1Y': '1y', '5Y': '5y'}
    config = ConfigMock()
    def calculate_rsi(x): return []
    def calculate_macd(x): return [], [], []
    def calculate_bollinger_bands(x): return [], []
    class SafeJSONEncoder(json.JSONEncoder): 
        def default(self, obj): return str(obj)

def clean_value(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (np.integer, np.floating)):
        return v.item()
    if isinstance(v, np.ndarray):
        return v.tolist()
    if hasattr(v, 'isoformat'):
        return v.isoformat()
    if hasattr(v, 'keys'):
        return clean_dict(dict(v))
    return v

def clean_dict(d):
    if isinstance(d, dict):
        return {k: clean_dict(clean_value(v)) for k, v in d.items()}
    elif isinstance(d, list):
        return [clean_dict(clean_value(x)) for x in d]
    return clean_value(d)

class ChartDataProcessor:
    @staticmethod
    def get_1d_chart(ticker):
        try:
            ticker_obj = yf.Ticker(ticker)
            data = ticker_obj.history(period='5d', interval='1m')
            if data.empty:
                return {'labels': [], 'prices': [], 'volumes': [], 'error': 'No data'}
            
            today_date = data.index[-1].date()
            
            # Use string-based slicing which is most robust in pandas for DatetimeIndex
            today_str = today_date.strftime('%Y-%m-%d')
            today_data = data.loc[today_str]
            market_data = today_data.between_time('09:30', '16:00')
            
            # Last close from data strictly before today
            hist_data = data.loc[:today_str].iloc[:-len(today_data)] if not today_data.empty else data.iloc[:-1]
            last_close = hist_data['Close'].iloc[-1] if not hist_data.empty else data['Open'].iloc[0]
            
            start_dt = pd.Timestamp.combine(today_date, datetime.time(9, 30))
            end_dt = pd.Timestamp.combine(today_date, datetime.time(16, 0))
            if data.index.tz is not None:
                start_dt = start_dt.tz_localize(data.index.tz)
                end_dt = end_dt.tz_localize(data.index.tz)

            full_range = pd.date_range(start=start_dt, end=end_dt, freq='1min')
            
            if market_data.empty:
                return {
                    'labels': [t.strftime('%H:%M') for t in full_range],
                    'prices': [], 'volumes': [], 'last_close': last_close
                }

            market_data = market_data.reindex(full_range)

            return {
                'labels': [x.strftime('%H:%M') for x in market_data.index],
                'prices': market_data['Close'].tolist(),
                'volumes': market_data['Volume'].tolist(),
                'last_close': last_close
            }
        except Exception as e:
            return {'labels': [], 'prices': [], 'error': f"Failed to fetch {ticker}: {str(e)}"}

    @staticmethod
    def get_historical_chart(ticker, tf):
        period = config.TIMEFRAME_MAP.get(tf, '1y')
        try:
            data = yf.Ticker(ticker).history(period=period)
            if data.empty: return {'labels': [], 'prices': [], 'volumes': [], 'error': 'No data'}
            return {
                'labels': [x.strftime('%Y-%m-%d %H:%M') for x in data.index],
                'prices': data['Close'].tolist(),
                'volumes': data['Volume'].tolist(),
                'high': data['High'].tolist(),
                'low': data['Low'].tolist(),
                'open': data['Open'].tolist()
            }
        except Exception as e:
            return {'labels': [], 'prices': [], 'error': str(e)}

class Handler(SimpleHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'X-Requested-With, Content-Type')
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path, qs = parsed.path, parse_qs(parsed.query)
        if not path.startswith('/api/'): return SimpleHTTPRequestHandler.do_GET(self)
        try:
            if path == '/api/quotes':
                from quotes import get_quotes
                return self.send_json(get_quotes(qs.get('tickers', ['SPY'])[0].split(',')))
            if path == '/api/chart':
                ticker, tf = qs.get('ticker', ['SPY'])[0], qs.get('tf', ['1D'])[0]
                return self.send_json(ChartDataProcessor.get_1d_chart(ticker) if tf == '1D' else ChartDataProcessor.get_historical_chart(ticker, tf))
            self.send_error(404, "API not found")
        except Exception as e: self.send_json({}, error=e)

    def send_json(self, data, error=None):
        self.send_response(500 if error else 200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(clean_dict(data) if not error else {'error': str(error)}, cls=SafeJSONEncoder).encode())

def run(port=9098):
    server = HTTPServer(('0.0.0.0', port), Handler)
    print(f'Alpha Terminal: http://localhost:{port}')
    server.serve_forever()

if __name__ == '__main__': run()
