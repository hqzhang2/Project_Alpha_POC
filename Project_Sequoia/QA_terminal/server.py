"""
Alpha Terminal Server - Version 1.3.3
Refactored for Stability and Header Injection
"""
from http.server import HTTPServer, SimpleHTTPRequestHandler
import json, math, os, socket, datetime, sys, numpy as np
from urllib.parse import urlparse, parse_qs
import yfinance as yf
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import config
    from indicators import calculate_rsi, calculate_macd, calculate_bollinger_bands
    from options import SafeJSONEncoder
except ImportError:
    class ConfigMock:
        DEFAULT_PORT = 9098
        QA_PORT = 9099
        HOST = '0.0.0.0'
        TIMEFRAME_MAP = {'1D': '5d', '1W': '1mo', '1M': '3mo', '3M': '6mo', 'YTD': 'ytd', '1Y': '1y', '5Y': '5y'}
    config = ConfigMock()
    def calculate_rsi(x): return [0]*len(x)
    def calculate_macd(x): return [0]*len(x), [0]*len(x), [0]*len(x)
    def calculate_bollinger_bands(x): return [0]*len(x), [0]*len(x)
    class SafeJSONEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, np.integer): return int(obj)
            if isinstance(obj, np.floating): return float(obj)
            if isinstance(obj, np.ndarray): return obj.tolist()
            if hasattr(obj, 'isoformat'): return obj.isoformat()
            return super().default(obj)

def clean_dict(d):
    if not isinstance(d, dict): return d
    result = {}
    for k, v in d.items():
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            result[k] = None
        elif isinstance(v, list):
            result[k] = [None if isinstance(x, float) and (math.isnan(x) or math.isinf(x)) else x for x in v]
        else:
            result[k] = v
    return result

class ChartDataProcessor:
    @staticmethod
    def get_1d_chart(ticker):
        try:
            t = yf.Ticker(ticker)
            # Fetch 5 days of 1-min data to ensure we have full session
            data = t.history(period='5d', interval='1m')
            if data.empty:
                return {'labels': [], 'prices': [], 'error': 'No data'}
            
            # Filter to today's market hours (09:30-16:00)
            import datetime
            today = datetime.datetime.now().date()
            data = data[data.index.date == today]
            if not data.empty:
                data = data.between_time('09:30', '16:00')
            
            # Generate full time axis from 09:30 to 16:00 (390 minutes)
            full_labels = []
            base_time = datetime.datetime.strptime(f"{today} 09:30", "%Y-%m-%d %H:%M")
            for i in range(391):
                full_labels.append(base_time.strftime('%H:%M'))
                base_time += datetime.timedelta(minutes=1)
            
            # Pad prices with nulls to match full axis
            prices = data['Close'].tolist() if not data.empty else []
            volumes = data['Volume'].tolist() if not data.empty else []
            
            # If we have partial data, pad with nulls to fill the day
            if len(prices) < 391:
                prices = prices + [None] * (391 - len(prices))
                volumes = volumes + [None] * (391 - len(volumes))
            elif len(prices) > 391:
                prices = prices[:391]
                volumes = volumes[:391]
            
            return {
                'labels': full_labels,
                'prices': prices,
                'volumes': volumes,
                'prev_close': t.info.get('previousClose'),
                'ticker': ticker
            }
        except Exception as e:
            return {'labels': [], 'prices': [], 'error': str(e)}

    @staticmethod
    def get_historical_chart(ticker, tf):
        try:
            data = yf.Ticker(ticker).history(period=config.TIMEFRAME_MAP.get(tf, '1y'))
            return {'labels': [x.strftime('%Y-%m-%d %H:%M') for x in data.index], 'prices': data['Close'].tolist(), 'volumes': data['Volume'].tolist()}
        except Exception as e: return {'labels': [], 'prices': [], 'error': str(e)}

class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path, qs = parsed.path, parse_qs(parsed.query)

        if path.startswith('/api/'):
            try:
                if path == '/api/quotes':
                    from quotes import get_quotes
                    return self.send_json(get_quotes(qs.get('tickers', ['SPY'])[0].split(',')))
                if path == '/api/options':
                    import options
                    return self.send_json(options.get_options_chain(qs.get('ticker', ['SPY'])[0], qs.get('expiry', [None])[0], use_cache=False))
                if path == '/api/screen':
                    import options
                    ticker = qs.get('ticker', ['SPY'])[0]
                    results = []
                    for expiry in options.get_expirations(ticker)[:8]:
                        chain = options.get_options_chain(ticker, expiry, use_cache=True)
                        for call in chain.get('calls', []):
                            call.update({'type': 'Call', 'expiry': expiry})
                            results.append(call)
                        for put in chain.get('puts', []):
                            put.update({'type': 'Put', 'expiry': expiry})
                            results.append(put)
                    return self.send_json({'ticker': ticker, 'results': results})
                if path == '/api/expirations':
                    import options
                    ticker = qs.get('ticker', ['SPY'])[0]
                    expirations = options.get_expirations(ticker)
                    standard = []
                    for e in expirations:
                        try:
                            dt = datetime.datetime.strptime(e, '%Y-%m-%d')
                            if dt.weekday() == 4 and 15 <= dt.day <= 21: standard.append({'date': e, 'label': dt.strftime('%b %Y') + " (Std)"})
                        except: continue
                    return self.send_json({'ticker': ticker, 'expirations': expirations, 'standard': standard})
                if path == '/api/chart':
                    ticker, tf = qs.get('ticker', ['SPY'])[0], qs.get('tf', ['1D'])[0]
                    return self.send_json(ChartDataProcessor.get_1d_chart(ticker) if tf == '1D' else ChartDataProcessor.get_historical_chart(ticker, tf))
                if path.startswith('/api/sec/financials'):
                    import sec_financials
                    action = qs.get('action', [None])[0]
                    if action == 'watchlist': return self.send_json(sec_financials.get_watchlist())
                    if action == 'add':
                        ticker = qs.get('ticker', [None])[0]
                        if ticker: sec_financials.add_to_watchlist(ticker)
                        return self.send_json({'status': 'added'})
                    if action == 'remove':
                        ticker = qs.get('ticker', [None])[0]
                        if ticker: sec_financials.remove_from_watchlist(ticker)
                        return self.send_json({'status': 'removed'})
                    return self.send_json(sec_financials.fetch_financials(qs.get('ticker', ['SPY'])[0], int(qs.get('periods', [8])[0]), qs.get('type', ['Q'])[0]))
                if path == '/api/ratio':
                    t1, t2 = qs.get('t1', ['XLE'])[0], qs.get('t2', ['SPY'])[0]
                    tf, sma_p = qs.get('tf', ['1Y'])[0], int(qs.get('sma', [20])[0])
                    return self.send_json(self.get_ratio_data(t1, t2, tf, sma_p))
                
                self.send_error(404, "API not found")
                return
            except Exception as e:
                self.send_json({}, error=e)
                return

        filename = path[1:] if path != '/' else 'dashboard.html'
        if os.path.exists(filename):
            if filename.endswith('.html'):
                with open(filename, 'r') as f: content = f.read()
                if '<div class="header">' in content and os.path.exists('header.html'):
                    with open('header.html', 'r') as hf: header_html = hf.read()
                    start = content.find('<div class="header">')
                    nav_idx = content.find('<div class="nav"', start)
                    if nav_idx != -1:
                        nav_end = content.find('</div>', nav_idx)
                        header_end = content.find('</div>', nav_end + 6) + 6
                        content = content[:start] + '<div class="header">' + header_html + '</div>' + content[header_end:]
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(content.encode())
                return
            return SimpleHTTPRequestHandler.do_GET(self)
        
        return SimpleHTTPRequestHandler.do_GET(self)

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
        else: display_df = df.tail(days if isinstance(days, int) else 365)
        result = {
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
        return clean_dict(result)

    def send_json(self, data, error=None):
        self.send_response(500 if error else 200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(clean_dict(data) if not error else {'error': str(error)}, cls=SafeJSONEncoder).encode())

def run(port=None):
    if port is None:
        port = int(os.environ.get('PORT', getattr(config, 'DEFAULT_PORT', 9098)))
    server = HTTPServer(('0.0.0.0', port), Handler)
    print(f'Alpha Terminal ({os.environ.get("ENV", "PROD")}): http://localhost:{port}')
    server.serve_forever()

if __name__ == '__main__':
    run()
