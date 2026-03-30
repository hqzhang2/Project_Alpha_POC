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

# Import config
import config
from indicators import calculate_rsi, calculate_macd, calculate_bollinger_bands

def find_free_port(start=config.DEFAULT_PORT, max_attempts=10):
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
    from decimal import Decimal
    import numpy as np
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
    if hasattr(v, 'keys'):  # dict-like (including frozendict)
        return clean_dict(dict(v))
    return v

def clean_dict(d):
    if isinstance(d, dict):
        return {k: clean_dict(clean_value(v)) for k, v in d.items()}
    elif isinstance(d, list):
        return [clean_dict(clean_value(x)) for x in d]
    return clean_value(d)

class Handler(SimpleHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'X-Requested-With, Content-Type')
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        
        # Handle static files
        if not path.startswith('/api/'):
            return SimpleHTTPRequestHandler.do_GET(self)

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
        
        if path == '/api/chart':
            return self.send_json(self.get_chart_data(qs.get('ticker', ['SPY'])[0], qs.get('tf', ['1Y'])[0]))
        
        if path == '/api/financials':
            # Force fresh import
            import importlib
            import sys
            if 'financials' in sys.modules:
                del sys.modules['financials']
            import importlib.util
            spec = importlib.util.spec_from_file_location("financials", os.path.dirname(os.path.abspath(__file__)) + "/financials.py")
            financials = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(financials)
            ticker = qs.get('ticker', ['SPY'])[0]
            periods = int(qs.get('periods', [10])[0])
            period_type = qs.get('type', ['Q'])[0]
            return self.send_json(financials.get_financials(ticker, periods, period_type))
        
        if path.startswith('/api/sec/financials'):
            # Use SEC EDGAR data - force fresh import to get latest fields
            import importlib
            import sys
            if 'sec_financials' in sys.modules:
                del sys.modules['sec_financials']
            # Clear any cached module
            for mod in list(sys.modules.keys()):
                if 'sec_financials' in mod:
                    del sys.modules[mod]
            
            # Get the directory where server.py is located
            server_dir = os.path.dirname(os.path.realpath(__file__))
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "sec_financials", 
                server_dir + "/sec_financials.py"
            )
            sec_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sec_mod)
            
            ticker = qs.get('ticker', ['SPY'])[0]
            periods = int(qs.get('periods', [8])[0])
            period_type = qs.get('type', ['Q'])[0]
            
            # Handle watchlist actions via this endpoint as well for convenience
            action = qs.get('action', [None])[0]
            if action == 'add':
                sec_mod.add_to_watchlist(ticker)
                return self.send_json({'status': 'added', 'ticker': ticker})
            elif action == 'remove':
                sec_mod.remove_from_watchlist(ticker)
                return self.send_json({'status': 'removed', 'ticker': ticker})
            elif action == 'watchlist':
                return self.send_json(sec_mod.get_watchlist())
            
            # Fetch SEC data
            sec_data = sec_mod.fetch_financials(ticker, periods, period_type)
            
            # If SEC data is missing fields, fill with Yahoo Finance
            if 'income' in sec_data or 'balance' in sec_data:
                import yfinance as yf
                try:
                    yt = yf.Ticker(ticker)
                    
                    # Fill missing balance sheet data
                    if sec_data.get('balance'):
                        bs = yt.balance_sheet.T.head(periods)
                        for i in range(len(sec_data['balance'])):
                            if i < len(bs):
                                b_row = bs.iloc[i]
                                # Map Yahoo columns to our schema (direct column access)
                                fill_map = {
                                    'term_debt': b_row.get('Long Term Debt'),
                                    'short_term_debt': b_row.get('Current Debt'),
                                    'ppe': b_row.get('Net PPE'),
                                    'marketable_securities': b_row.get('Marketable Securities')
                                }
                                for scol, val in fill_map.items():
                                    if (scol not in sec_data['balance'][i] or sec_data['balance'][i].get(scol) is None) and val is not None and not pd.isna(val):
                                        sec_data['balance'][i][scol] = val
                                        sec_data['balance'][i][scol + '_yahoo'] = True
                    
                    # Fill missing cashflow data
                    if sec_data.get('cashflow'):
                        cf = yt.cashflow.T.head(periods)
                        for i in range(len(sec_data['cashflow'])):
                            if i < len(cf):
                                c_row = cf.iloc[i]
                                fill_map = {
                                    'operating_cf': c_row.get('Operating Cash Flow'),
                                    'capex': c_row.get('Capital Expenditure'),
                                    'free_cf': c_row.get('Free Cash Flow'),
                                    'depreciation': c_row.get('Depreciation')
                                }
                                for scol, val in fill_map.items():
                                    if (scol not in sec_data['cashflow'][i] or sec_data['cashflow'][i].get(scol) is None) and val is not None and not pd.isna(val):
                                        sec_data['cashflow'][i][scol] = val
                                        sec_data['cashflow'][i][scol + '_yahoo'] = True
                except Exception as e:
                    print(f"Yahoo fill error: {e}")
            
            return self.send_json(sec_data)
        
        if path == '/api/financials/refresh':
            from financials import pull_financials
            ticker = qs.get('ticker', ['SPY'])[0]
            pull_financials(ticker)
            return self.send_json({'status': 'ok', 'message': f'Pulled fresh data for {ticker}'})
        
        if path == '/api/financials/watchlist':
            from financials import get_watchlist, add_to_watchlist, remove_from_watchlist
            action = qs.get('action', ['list'])[0]
            ticker = qs.get('ticker', [None])[0]
            if action == 'add' and ticker:
                add_to_watchlist(ticker)
            elif action == 'remove' and ticker:
                remove_from_watchlist(ticker)
            return self.send_json(get_watchlist())
        
        self.send_error(404, "API not found")

    def get_chart_data(self, ticker, tf):
        period = config.TIMEFRAME_MAP.get(tf, '1y')
        
        try:
            data = yf.Ticker(ticker).history(period=period)
            if data.empty:
                return {'labels': [], 'prices': [], 'volumes': [], 'error': 'No data'}
            
            return {
                'labels': [x.strftime('%Y-%m-%d %H:%M') for x in data.index],
                'prices': data['Close'].tolist(),
                'volumes': data['Volume'].tolist(),
                'high': data['High'].tolist(),
                'low': data['Low'].tolist(),
                'open': data['Open'].tolist()
            }
        except Exception as e:
            return {'labels': [], 'prices': [], 'error': f"Failed to fetch {ticker}: {str(e)}"}

    def get_ratio_data(self, t1, t2, tf, sma_period):
        # Increased fetch period to 3 years to ensure 200 SMA is stable at the start of a 1Y or 2Y view
        fetch_period = '3y' if tf != '5Y' else 'max'
        
        d1 = yf.Ticker(t1).history(period=fetch_period)['Close']
        d2 = yf.Ticker(t2).history(period=fetch_period)['Close']
        
        # Align data
        df = pd.DataFrame({'t1': d1, 't2': d2}).dropna()
        df['ratio'] = df['t1'] / df['t2']
        
        # Indicators calculated on full history to ensure accuracy at the start of display
        df['sma'] = df['ratio'].rolling(window=sma_period).mean()
        df['rsi'] = calculate_rsi(df['ratio'])
        macd, signal, hist = calculate_macd(df['ratio'])
        upper, lower = calculate_bollinger_bands(df['ratio'])
        
        df['macd'] = macd
        df['macd_signal'] = signal
        df['macd_hist'] = hist
        df['upper'] = upper
        df['lower'] = lower

        # Slicing for display timeframe
        period_map = {'1M': 30, '3M': 90, '6M': 180, 'YTD': 'ytd', '1Y': 365, '5Y': 1825}
        days = period_map.get(tf, 365)
        
        if days == 'ytd':
            # Use fixed year for the POC or current year
            current_year = datetime.date.today().year
            start_date = pd.Timestamp(year=current_year, month=1, day=1)
            # Ensure start_date matches timezone of data index
            if df.index.tz is not None:
                start_date = start_date.tz_localize(df.index.tz)
            display_df = df[df.index >= start_date]
        else:
            display_df = df.tail(days)
        
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
            't1_name': t1,
            't2_name': t2
        }
    
    def send_json(self, data, error=None):
        if error:
            self.send_response(500)
            data = {'error': str(error)}
        else:
            self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(clean_dict(data)).encode())

def run(port=config.DEFAULT_PORT):
    os.chdir(os.path.dirname(os.path.abspath(__file__)) or '.')
    server = HTTPServer((config.HOST, port), Handler)
    print(f'Alpha Terminal: http://localhost:{port}')
    server.serve_forever()

if __name__ == '__main__':
    port = 9098
    run(port)
