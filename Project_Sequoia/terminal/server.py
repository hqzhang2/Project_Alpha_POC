"""
Alpha Terminal Server - Version 1.3.1
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

# Import config and indicators
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
            today_str = today_date.strftime('%Y-%m-%d')
            today_data = data.loc[today_str]
            market_data = today_data.between_time('09:30', '16:00')
            
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

        # Handle static file requests (HTML, JS, CSS)
        if not path.startswith('/api/'):
            # Intercept HTML files to inject common header
            if path.endswith('.html') or path == '/':
                filename = path[1:] if path != '/' else 'dashboard.html'
                if os.path.exists(filename):
                    with open(filename, 'r') as f:
                        content = f.read()
                    
                    # Inject header if <div class="header"> exists
                    if '<div class="header">' in content and os.path.exists('header.html'):
                        with open('header.html', 'r') as hf:
                            header_html = hf.read()
                        
                        # Simplified logic: find <div class="header"> and its matching end
                        start_tag = '<div class="header">'
                        if start_tag in content:
                            start_pos = content.find(start_tag)
                            # Find the end of the header block. In our files, it's followed by a newline and <div class="toolbar"> or <div class="main">
                            # We look for the closing </div> of the <div class="nav"> section which is the last part of the header.
                            nav_end = content.find('</div>', content.find('<div class="nav"', start_pos))
                            header_end = content.find('</div>', nav_end + 6) + 6
                            
                            new_content = content[:start_pos] + '<div class="header">' + header_html + '</div>' + content[header_end:]
                        else:
                            new_content = content
                        
                        self.send_response(200)
                        self.send_header('Content-type', 'text/html')
                        self.end_headers()
                        self.wfile.write(new_content.encode())
                        return
            
            return SimpleHTTPRequestHandler.do_GET(self)
        try:
            # 1. Ratio Analysis
            if path == '/api/ratio':
                t1 = qs.get('t1', ['XLE'])[0]
                t2 = qs.get('t2', ['SPY'])[0]
                tf = qs.get('tf', ['1Y'])[0]
                sma_p = int(qs.get('sma', [20])[0])
                return self.send_json(self.get_ratio_data(t1, t2, tf, sma_p))

            # 2. Quotes
            if path == '/api/quotes':
                from quotes import get_quotes
                return self.send_json(get_quotes(qs.get('tickers', ['SPY'])[0].split(',')))
            
            # 3. Options (OMON)
            if path == '/api/options':
                import options
                ticker = (qs.get('ticker', []) + qs.get('symbol', []))[0] or 'SPY'
                return self.send_json(options.get_options_chain(ticker, qs.get('expiry', [None])[0], use_cache=False))
            
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

            # 4. Charts (Dashboard)
            if path == '/api/chart':
                ticker = qs.get('ticker', ['SPY'])[0]
                tf = qs.get('tf', ['1D'])[0]
                return self.send_json(ChartDataProcessor.get_1d_chart(ticker) if tf == '1D' else ChartDataProcessor.get_historical_chart(ticker, tf))

            # 5. Financials (SEC)
            if path.startswith('/api/sec/financials'):
                import sec_financials
                action = qs.get('action', [None])[0]
                
                # Handle watchlist actions
                if action == 'watchlist':
                    return self.send_json(sec_financials.get_watchlist())
                if action == 'add':
                    ticker = qs.get('ticker', [None])[0]
                    if ticker:
                        sec_financials.add_to_watchlist(ticker)
                    return self.send_json({'status': 'added'})
                if action == 'remove':
                    ticker = qs.get('ticker', [None])[0]
                    if ticker:
                        sec_financials.remove_from_watchlist(ticker)
                    return self.send_json({'status': 'removed'})
                
                # Default: fetch financials
                ticker = qs.get('ticker', ['SPY'])[0]
                periods = int(qs.get('periods', [8])[0])
                period_type = qs.get('type', ['Q'])[0]
                return self.send_json(sec_financials.fetch_financials(ticker, periods, period_type))

            self.send_error(404, "API not found")
        except Exception as e:
            self.send_json({}, error=e)

    def get_ratio_data(self, t1, t2, tf, sma_period):
        fetch_period = '3y' if tf != '5Y' else 'max'
        d1 = yf.Ticker(t1).history(period=fetch_period)['Close']
        d2 = yf.Ticker(t2).history(period=fetch_period)['Close']
        df = pd.DataFrame({'t1': d1, 't2': d2}).dropna()
        df['ratio'] = df['t1'] / df['t2']
        df['sma'] = df['ratio'].rolling(window=sma_period).mean()
        df['rsi'] = calculate_rsi(df['ratio'])
        macd, signal, hist = calculate_macd(df['ratio'])
        upper, lower = calculate_bollinger_bands(df['ratio'])
        df['macd'], df['macd_signal'], df['macd_hist'] = macd, signal, hist
        df['upper'], df['lower'] = upper, lower
        period_map = {'1M': 30, '3M': 90, '6M': 180, 'YTD': 'ytd', '1Y': 365, '5Y': 1825}
        days = period_map.get(tf, 365)
        if days == 'ytd':
            start_date = pd.Timestamp(year=datetime.date.today().year, month=1, day=1)
            if df.index.tz: start_date = start_date.tz_localize(df.index.tz)
            display_df = df[df.index >= start_date]
        else: display_df = df.tail(days)
        return {
            'labels': [x.strftime('%Y-%m-%d') for x in display_df.index],
            'ratio': display_df['ratio'].tolist(),
            'sma': display_df['sma'].tolist(),
            'rsi': display_df['rsi'].tolist(),
            'macd': display_df['macd'].tolist(),
            'macd_signal': display_df['macd_signal'].tolist(),
            'macd_hist': display_df['macd_hist'].tolist(),
            'upper': display_df['upper'].tolist(),
            'lower': display_df['lower'].tolist(),
            't1_name': t1, 't2_name': t2
        }

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

if __name__ == '__main__':
    run()
