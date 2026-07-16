#!/usr/bin/env python3
"""
Alpha Terminal Server - QA Environment
Refactored for stability, observability, and maintainability.
"""

from http.server import HTTPServer, SimpleHTTPRequestHandler
import json
import math
import os
import sys
import signal
import time
import threading
from datetime import datetime
from urllib.parse import urlparse, parse_qs
import logging
from logging.handlers import RotatingFileHandler

# Ensure local imports work
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Third-party imports (with graceful fallback)
try:
    import numpy as np
    import pandas as pd
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False
    np = pd = yf = None

# Local imports with fallbacks
try:
    import config
    from indicators import calculate_rsi, calculate_macd, calculate_bollinger_bands
    from options import SafeJSONEncoder
except ImportError:
    class ConfigMock:
        DEFAULT_PORT = 9098
        QA_PORT = 9099
        HOST = '0.0.0.0'
        TIMEFRAME_MAP = {
            '1D': '1d', '1W': '1wk', '1M': '1mo', '3M': '3mo',
            'YTD': 'ytd', '1Y': '1y', '5Y': '5y'
        }
    config = ConfigMock()
    def calculate_rsi(x): return [0]*len(x) if hasattr(x, '__len__') else []
    def calculate_macd(x): return [0]*len(x), [0]*len(x), [0]*len(x)
    def calculate_bollinger_bands(x): return [0]*len(x), [0]*len(x)
    class SafeJSONEncoder(json.JSONEncoder):
        def default(self, obj):
            if np and isinstance(obj, (np.integer,)): return int(obj)
            if np and isinstance(obj, (np.floating,)): return float(obj)
            if np and isinstance(obj, np.ndarray): return obj.tolist()
            if hasattr(obj, 'isoformat'): return obj.isoformat()
            return super().default(obj)


# ============================================================================
# Configuration & Constants
# ============================================================================

ENV = os.environ.get('ENV', 'QA')
PORT = int(os.environ.get('PORT', getattr(config, 'QA_PORT', 9099)))
HOST = getattr(config, 'HOST', '0.0.0.0')
CACHE_TTL = int(os.environ.get('YF_CACHE_TTL', '300'))  # 5 min default
HEALTH_CHECK_INTERVAL = 30

# ============================================================================
# Logging Setup
# ============================================================================

log_dir = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, f'server_{PORT}.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('alpha-terminal')

# ============================================================================
# Caching
# ============================================================================

class TTLCache:
    """Thread-safe TTL cache."""
    def __init__(self, ttl_seconds=300):
        self._cache = {}
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
    
    def get(self, key):
        with self._lock:
            if key in self._cache:
                value, expiry = self._cache[key]
                if time.time() < expiry:
                    return value
                del self._cache[key]
            return None
    
    def set(self, key, value):
        with self._lock:
            self._cache[key] = (value, time.time() + self._ttl)
    
    def clear(self):
        with self._lock:
            self._cache.clear()


_quote_cache = TTLCache(ttl_seconds=CACHE_TTL)

# ============================================================================
# Utility Functions
# ============================================================================

def clean_dict(d):
    """Recursively clean dict of NaN/inf values."""
    if not isinstance(d, dict):
        return d
    result = {}
    for k, v in d.items():
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            result[k] = None
        elif isinstance(v, list):
            result[k] = [None if isinstance(x, float) and (math.isnan(x) or math.isinf(x)) else x for x in v]
        elif isinstance(v, dict):
            result[k] = clean_dict(v)
        else:
            result[k] = v
    return result


def safe_float(val):
    """Safely convert to float, returning None for invalid."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


# ============================================================================
# Chart Data Processor
# ============================================================================

class ChartDataProcessor:
    @staticmethod
    def get_1d_chart(ticker: str) -> dict:
        """Get intraday 1-minute chart for current trading day."""
        if not YFINANCE_AVAILABLE:
            return {'labels': [], 'prices': [], 'volumes': [], 'error': 'yfinance not available'}
        
        try:
            t = yf.Ticker(ticker)
            today = datetime.now().date()
            
            # Fetch 5 days to ensure we have today's data
            data = t.history(period='5d', interval='1m')
            if data.empty:
                return {'labels': [], 'prices': [], 'volumes': [], 'error': 'No data', 'ticker': ticker}
            
            # Filter to today's market hours (09:30-16:00 ET)
            data = data[data.index.date == today]
            if not data.empty:
                data = data.between_time('09:30', '16:00')
            
            # Generate full 391-minute axis (09:30 to 16:00 inclusive)
            full_labels = []
            base = datetime.strptime(f"{today} 09:30", "%Y-%m-%d %H:%M")
            for i in range(391):
                full_labels.append(base.strftime('%H:%M'))
                base += pd.Timedelta(minutes=1)
            
            prices = data['Close'].tolist() if not data.empty else []
            volumes = data['Volume'].tolist() if not data.empty else []
            
            # Pad or trim to 391 points
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
                'prev_close': safe_float(t.info.get('previousClose')),
                'ticker': ticker
            }
        except Exception as e:
            logger.error(f"Chart error for {ticker}: {e}")
            return {'labels': [], 'prices': [], 'volumes': [], 'error': str(e), 'ticker': ticker}
    
    @staticmethod
    def get_historical_chart(ticker: str, tf: str) -> dict:
        """Get historical chart for given timeframe."""
        if not YFINANCE_AVAILABLE:
            return {'labels': [], 'prices': [], 'volumes': [], 'error': 'yfinance not available'}
        
        try:
            period = config.TIMEFRAME_MAP.get(tf, '1y')
            data = yf.Ticker(ticker).history(period=period)
            return {
                'labels': [x.strftime('%Y-%m-%d') for x in data.index],
                'prices': data['Close'].tolist(),
                'volumes': data['Volume'].tolist(),
                'ticker': ticker
            }
        except Exception as e:
            logger.error(f"Historical chart error for {ticker} ({tf}): {e}")
            return {'labels': [], 'prices': [], 'volumes': [], 'error': str(e), 'ticker': ticker}


# ============================================================================
# Request Handler
# ============================================================================

class Handler(SimpleHTTPRequestHandler):
    """HTTP request handler with API routing."""
    
    # API route mapping
    API_ROUTES = {
        '/api/etf-holdings': 'handle_etf_holdings',
        '/api/quotes': 'handle_quotes',
        '/api/news/top': 'handle_news_top',
        '/api/news/cn': 'handle_news_cn',
        '/api/prediction': 'handle_prediction',
        '/api/options': 'handle_options',
        '/api/screen': 'handle_screen',
        '/api/expirations': 'handle_expirations',
        '/api/chart': 'handle_chart',
        '/api/estimates': 'handle_estimates',
        '/api/ratio': 'handle_ratio',
        '/api/health': 'handle_health',
    }
    
    def do_GET(self):
        parsed = urlparse(self.path)
        path, qs = parsed.path, parse_qs(parsed.query)
        
        # Route API calls
        if path.startswith('/api/'):
            handler_name = self.API_ROUTES.get(path)
            if handler_name and hasattr(self, handler_name):
                try:
                    getattr(self, handler_name)(qs)
                except Exception as e:
                    logger.exception(f"API error on {path}")
                    self.send_json({'error': str(e)}, status=500)
                return
            else:
                self.send_error(404, "API not found")
                return
        
        # Serve static files
        filename = path[1:] if path != '/' else 'dashboard.html'
        if os.path.exists(filename):
            self.serve_file(filename)
            return
        
        self.send_error(404, "Not found")
    
    def serve_file(self, filename):
        if filename.endswith('.html'):
            with open(filename, 'r') as f:
                content = f.read()
            # Inject header if present
            if '<div class="header">' in content and os.path.exists('header.html'):
                with open('header.html', 'r') as hf:
                    header_html = hf.read()
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
        else:
            SimpleHTTPRequestHandler.do_GET(self)
    
    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(clean_dict(data), cls=SafeJSONEncoder).encode())
    
    # --- API Handlers ---
    
    def handle_health(self, qs):
        self.send_json({
            'status': 'ok',
            'env': ENV,
            'port': PORT,
            'timestamp': datetime.now().isoformat(),
            'yfinance': YFINANCE_AVAILABLE
        })
    
    def handle_etf_holdings(self, qs):
        if not YFINANCE_AVAILABLE:
            self.send_json({'error': 'yfinance not available'}, status=503)
            return
        ticker = qs.get('ticker', ['SPY'])[0].upper()
        limit = int(qs.get('limit', ['50'])[0])
        try:
            tick = yf.Ticker(ticker)
            if hasattr(tick, 'funds_data') and tick.funds_data.top_holdings is not None:
                holdings = tick.funds_data.top_holdings.head(limit)
                holdings_dict = {}
                if 'Holding Percent' in holdings.columns:
                    for symbol, weight in holdings['Holding Percent'].items():
                        holdings_dict[str(symbol)] = float(weight)
                self.send_json({'ticker': ticker, 'holdings': holdings_dict})
            else:
                self.send_json({'error': f'No fund holdings data for {ticker}'})
        except Exception as e:
            self.send_json({'error': str(e)}, status=500)
    
    def handle_quotes(self, qs):
        from quotes import get_quotes
        tickers = qs.get('tickers', ['SPY'])[0].split(',')
        self.send_json(get_quotes(tickers))
    
    def handle_news_top(self, qs):
        import news
        cat = qs.get('cat', ['general'])[0]
        self.send_json(news.get_top_news(cat))
    
    def handle_news_cn(self, qs):
        import news
        self.send_json(news.get_cn_news())
    
    def handle_prediction(self, qs):
        import prediction
        self.send_json(prediction.get_predictions())
    
    def handle_options(self, qs):
        import options
        ticker = qs.get('ticker', ['SPY'])[0]
        expiry = qs.get('expiry', [None])[0]
        self.send_json(options.get_options_chain(ticker, expiry, use_cache=False))
    
    def handle_screen(self, qs):
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
        self.send_json({'ticker': ticker, 'results': results})
    
    def handle_expirations(self, qs):
        import options
        ticker = qs.get('ticker', ['SPY'])[0]
        expirations = options.get_expirations(ticker)
        standard = []
        for e in expirations:
            try:
                dt = datetime.strptime(e, '%Y-%m-%d')
                if dt.weekday() == 4 and 15 <= dt.day <= 21:
                    standard.append({'date': e, 'label': dt.strftime('%b %Y') + " (Std)"})
            except:
                continue
        self.send_json({'ticker': ticker, 'expirations': expirations, 'standard': standard})
    
    def handle_chart(self, qs):
        ticker = qs.get('ticker', ['SPY'])[0]
        tf = qs.get('tf', ['1D'])[0]
        if tf == '1D':
            data = ChartDataProcessor.get_1d_chart(ticker)
        else:
            data = ChartDataProcessor.get_historical_chart(ticker, tf)
        self.send_json(data)
    
    def handle_estimates(self, qs):
        import estimates
        ticker = qs.get('ticker', ['SPY'])[0]
        self.send_json(estimates.get_estimates(ticker))
    
    def handle_ratio(self, qs):
        t1, t2 = qs.get('t1', ['XLE'])[0], qs.get('t2', ['SPY'])[0]
        tf, sma_p = qs.get('tf', ['1Y'])[0], int(qs.get('sma', ['20'])[0])
        self.send_json(self.get_ratio_data(t1, t2, tf, sma_p))
    
    def get_ratio_data(self, t1, t2, tf, sma_period):
        if not YFINANCE_AVAILABLE:
            return {'error': 'yfinance not available'}
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
            start_date = pd.Timestamp(year=datetime.now().year, month=1, day=1)
            if df.index.tz:
                start_date = start_date.tz_localize(df.index.tz)
            display_df = df[df.index >= start_date]
        else:
            display_df = df.tail(days if isinstance(days, int) else 365)
        
        return clean_dict({
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
        })
    
    def log_message(self, format, *args):
        logger.info("%s - %s" % (self.address_string(), format % args))


# ============================================================================
# Server Management
# ============================================================================

class AlphaTerminalServer:
    def __init__(self, host=HOST, port=PORT):
        self.host = host
        self.port = port
        self.server = None
        self.server_thread = None
        self._shutdown = threading.Event()
    
    def start(self):
        """Start the HTTP server in a background thread."""
        self.server = HTTPServer((self.host, self.port), Handler)
        self.server_thread = threading.Thread(target=self._serve, daemon=True)
        self.server_thread.start()
        logger.info(f"Alpha Terminal ({ENV}): http://{self.host}:{self.port}")
    
    def _serve(self):
        self.server.serve_forever(poll_interval=0.5)
    
    def stop(self):
        """Graceful shutdown."""
        logger.info("Shutting down server...")
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        self._shutdown.set()
        if self.server_thread:
            self.server_thread.join(timeout=5)
        logger.info("Server stopped")


def run(port=None):
    """Entry point for direct execution."""
    server = AlphaTerminalServer(port=port or PORT)
    
    def signal_handler(sig, frame):
        logger.info(f"Received signal {sig}")
        server.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    server.start()
    try:
        while not server._shutdown.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()


if __name__ == '__main__':
    run()