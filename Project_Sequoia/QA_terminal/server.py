#!/usr/bin/env python3
"""
Alpha Terminal Server - QA Environment
Refactored for stability, observability, and maintainability.
"""

# --- Environment hardening (PYTHONPATH isolation guard) -------------------
# The runtime must NOT inherit a foreign site-packages path. A leaked
# PYTHONPATH pointing at another interpreter's packages (e.g. a 3.11 venv
# from a parent shell) makes this 3.9 process import 3.11-only wheels
# (urllib3 using PEP 604 `X | Y` syntax) and crash with
#   TypeError: unsupported operand type(s) for |: 'type' and 'type'
# on `import requests` -> HTTP 500 on /api/sec/financials.
# Strip any PYTHONPATH that references a python version / venv that is not
# compatible, before any third-party import happens.
import os as _os
_PY = _os.path.realpath(_os.path.dirname(_os.__file__))
_bad = []
for _p in _os.environ.get("PYTHONPATH", "").split(":"):
    _p = _p.strip()
    if not _p:
        continue
    # Drop paths that resolve into a *different* interpreter's tree.
    try:
        _rp = _os.path.realpath(_p)
    except OSError:
        _rp = _p
    if _rp == _PY or _rp.startswith(_PY + _os.sep):
        continue  # own site-packages -> keep
    # Drop anything that looks like a foreign venv / versioned site-packages.
    if "hermes-agent" in _rp or "venv" in _rp.split(_os.sep) or "site-packages" in _rp:
        _bad.append(_p)
        continue
    # keep benign local paths
if _bad:
    _new = ":".join(
        p for p in _os.environ.get("PYTHONPATH", "").split(":")
        if p.strip() and p.strip() not in _bad
    )
    if _new:
        _os.environ["PYTHONPATH"] = _new
    else:
        _os.environ.pop("PYTHONPATH", None)
    import sys as _sys
    _sys.path = [p for p in _sys.path if p not in _bad]

from http.server import HTTPServer, SimpleHTTPRequestHandler, ThreadingHTTPServer
import email.utils
import json
import math
import os
import sys
import signal
import time
import threading
import queue
from datetime import datetime, timedelta
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
DOCROOT = os.path.realpath(os.path.dirname(os.path.abspath(__file__)))
CACHE_TTL = int(os.environ.get('YF_CACHE_TTL', '60'))  # 60s default; 15s poll stays fresh
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
# Off-thread OI snapshot worker
# ============================================================================
#
# Watchlist adds used to snapshot option chains SYNCHRONOUSLY on the request
# thread — 4 expiries of yfinance chain downloads ≈ 8-25s of terminal freeze.
# Now the handler enqueues and returns immediately; the worker does the chain
# work in the background and stores the result for a status poll.

_oi_queue = queue.Queue()
_oi_results = {}
_oi_lock = threading.Lock()


def _oi_worker():
    while True:
        ticker = _oi_queue.get()
        try:
            import snapshot_oi, options_data, sentiment_collect
            provider = options_data.get_provider()
            asof = snapshot_oi.last_trading_day(ticker)
            contracts = snapshot_oi.snapshot_ticker(provider, ticker, asof)
            reading = sentiment_collect.compute_oi_ratio(asof, ticker)
            with _oi_lock:
                _oi_results[ticker] = {'status': 'done', 'asof': asof,
                                       'contracts': contracts, 'reading': reading}
        except Exception as e:
            logger.exception(f"oi snapshot failed for {ticker}")
            with _oi_lock:
                _oi_results[ticker] = {'status': 'error', 'error': str(e)}
        finally:
            _oi_queue.task_done()


threading.Thread(target=_oi_worker, daemon=True, name='oi-snapshot-worker').start()

# ============================================================================
# Utility Functions
# ============================================================================

def _is_bad_float(x):
    """True for NaN/inf, covering both Python float and numpy float64.

    clean_dict's old isinstance(x, float) check missed np.float64 (numpy
    floats are not Python float subclasses), letting bare NaN literals into
    JSON responses — browsers reject them (GME earnings estimates broke the
    whole page: res.json() threw). float() conversion + math.isnan handles
    every numeric type; non-numerics fall through untouched.
    """
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return math.isnan(f) or math.isinf(f)


def clean_dict(d):
    """Recursively clean dict of NaN/inf values (incl. numpy float64)."""
    if not isinstance(d, dict):
        return d
    result = {}
    for k, v in d.items():
        if _is_bad_float(v):
            result[k] = None
        elif isinstance(v, list):
            result[k] = [None if _is_bad_float(x) else x for x in v]
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
        """Get historical chart for given timeframe.

        Includes pc_ratio: put/call OI ratio per date from the sentiment store
        (oi_store readings), aligned to the chart's date labels. None where the
        ticker has no OI reading that day. Fail-open: [] on any error.
        """
        if not YFINANCE_AVAILABLE:
            return {'labels': [], 'prices': [], 'volumes': [], 'error': 'yfinance not available'}

        try:
            period = config.TIMEFRAME_MAP.get(tf, '1y')
            data = yf.Ticker(ticker).history(period=period)
            labels = [x.strftime('%Y-%m-%d') for x in data.index]
            return {
                'labels': labels,
                'prices': data['Close'].tolist(),
                'volumes': data['Volume'].tolist(),
                'pc_ratio': ChartDataProcessor._pc_ratio_for(ticker, labels),
                'ticker': ticker
            }
        except Exception as e:
            logger.error(f"Historical chart error for {ticker} ({tf}): {e}")
            return {'labels': [], 'prices': [], 'volumes': [], 'error': str(e), 'ticker': ticker}

    @staticmethod
    def _pc_ratio_for(ticker: str, labels: list) -> list:
        """Put/call OI ratio per date for a ticker, from sentiment readings.

        Returns a list aligned to `labels` (None where no OI reading that day).
        Readings dated on/after a label's date attach to it (an on-demand
        snapshot on a weekend is dated Saturday but reflects Friday's close —
        it belongs on Friday's bar, not nowhere).
        """
        try:
            import sentiment_db
            rows = sentiment_db.query_readings(scope='ticker', ticker=ticker,
                                               metric='put_call_oi_ratio')
            # readings sorted by date; advance a pointer as labels progress
            dated = sorted((r['asof_date'], r['value']) for r in rows
                           if r.get('value') is not None)
            out, i, cur = [], 0, None
            for lab in labels:
                while i < len(dated) and dated[i][0] <= lab:
                    cur = dated[i][1]
                    i += 1
                out.append(cur)
            return out if any(v is not None for v in out) else []
        except Exception as e:
            logger.error(f"pc_ratio lookup failed for {ticker}: {e}")
            return []


# ============================================================================
# Request Handler
# ============================================================================

class Handler(SimpleHTTPRequestHandler):
    """HTTP request handler with API routing."""
    protocol_version = 'HTTP/1.1'

    # Dynamic module route table — built by _discover_module_routes()
    MODULE_ROUTES = {}

    @classmethod
    def _discover_module_routes(cls):
        """Import known modules and collect their ROUTES dicts."""
        modules = {
            'year_highs': 'year_highs',
            'year_lows': 'year_lows',
            'news': 'news',
            'sentiment': 'sentiment',
            'option_screener': 'option_screener',
            'fundamental_screener': 'fundamental_screener',
            'macro': 'macro',
            'estimates': 'estimates',
        }
        routes = {}
        for mod_name, import_path in modules.items():
            try:
                mod = __import__(import_path, fromlist=['ROUTES'])
                if hasattr(mod, 'ROUTES'):
                    routes.update(mod.ROUTES)
            except ImportError:
                pass
        cls.MODULE_ROUTES = routes

    # Static API route mapping (legacy — new routes should use module ROUTES)
    API_ROUTES = {
        '/api/etf-holdings': 'handle_etf_holdings',
        '/api/quotes': 'handle_quotes',
        '/api/prediction': 'handle_prediction',
        '/api/options': 'handle_options',
        '/api/screen': 'handle_screen',
        '/api/expirations': 'handle_expirations',
        '/api/chart': 'handle_chart',
        '/api/ratio': 'handle_ratio',
        '/api/health': 'handle_health',
        '/api/oi/snapshot': 'handle_oi_snapshot',
        '/health': 'handle_health',  # deploy_prod.sh checks /health on all services
    }

    # Prefix-based routes (for nested APIs like /api/sec/financials)
    API_PREFIX_ROUTES = {
        '/api/sec/financials': 'handle_sec_financials',
    }
    
    def do_GET(self):
        parsed = urlparse(self.path)
        path, qs = parsed.path, parse_qs(parsed.query)
        
        # Route API calls
        if path.startswith('/api/') or path == '/health':
            # Check module routes first (R2)
            handler_name = self.MODULE_ROUTES.get(path)
            if handler_name and hasattr(self, handler_name):
                try:
                    getattr(self, handler_name)(qs)
                except Exception as e:
                    logger.exception(f"API error on {path}")
                    self.send_json({'error': str(e)}, status=500)
                return

            # Check static routes (legacy)
            handler_name = self.API_ROUTES.get(path)
            if handler_name and hasattr(self, handler_name):
                try:
                    getattr(self, handler_name)(qs)
                except Exception as e:
                    logger.exception(f"API error on {path}")
                    self.send_json({'error': str(e)}, status=500)
                return
            
            # Check prefix routes
            for prefix, handler_name in self.API_PREFIX_ROUTES.items():
                if path.startswith(prefix):
                    if hasattr(self, handler_name):
                        try:
                            getattr(self, handler_name)(qs)
                        except Exception as e:
                            logger.exception(f"API error on {path}")
                            self.send_json({'error': str(e)}, status=500)
                        return
            
            self.send_error(404, "API not found")
            return
        
        # Serve static files
        filename = path[1:] if path != '/' else 'dashboard.html'
        # Traversal guard: resolve against the docroot and require containment
        candidate = os.path.realpath(os.path.join(DOCROOT, filename))
        if not (candidate == DOCROOT or candidate.startswith(DOCROOT + os.sep)):
            self.send_error(404, "Not found")
            return
        if os.path.exists(candidate):
            self.serve_file(candidate)
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
            body = content.encode()
            last_mod = email.utils.formatdate(os.path.getmtime(filename), usegmt=True)
            if self.headers.get('If-Modified-Since') == last_mod:
                self.send_response(304)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Last-Modified', last_mod)
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(body)
        else:
            SimpleHTTPRequestHandler.do_GET(self)
    
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('X-Frame-Options', 'ALLOWALL')
        super().end_headers()

    def send_json(self, data, status=200, headers=None):
        """Send JSON with Content-Length (required for HTTP/1.1 keep-alive;
        without it clients hang waiting for the body end)."""
        body = json.dumps(clean_dict(data), cls=SafeJSONEncoder).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)
    
    # --- API Handlers ---
    
    def handle_health(self, qs):
        self.send_json({
            'status': 'ok',
            'env': ENV,
            'port': PORT,
            'timestamp': datetime.now().isoformat(),
            'yfinance': YFINANCE_AVAILABLE
        })
    
    def handle_oi_snapshot(self, qs):
        """On-demand OI snapshot for one ticker (dashboard watchlist add).

        Enqueues the snapshot to the background worker and returns
        immediately — a synchronous chain download used to occupy the request
        thread for 8-25s. The JS refetches the chart ~30s later to pick up
        the P/C reading. GET ?action=status&ticker=X polls the worker result.
        """
        ticker = (qs.get('ticker', [''])[0] or '').upper()
        if not ticker:
            self.send_json({'error': 'ticker required'}, status=400)
            return
        if qs.get('action', [None])[0] == 'status':
            with _oi_lock:
                self.send_json({'ticker': ticker, **_oi_results.get(ticker, {'status': 'pending'})})
            return
        with _oi_lock:
            _oi_results.pop(ticker, None)
        _oi_queue.put(ticker)
        self.send_json({'ticker': ticker, 'status': 'started'})
    
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
        raw = (qs.get('tickers') or [''])[0]
        tickers = sorted({t.strip().upper() for t in raw.split(',') if t.strip()})
        if not tickers:
            self.send_json({'error': 'tickers required'}, status=400)
            return
        if len(tickers) > 50:
            self.send_json({'error': 'too many tickers (max 50)'}, status=400)
            return
        key = ','.join(tickers)
        cached = _quote_cache.get(key)
        if cached is not None:
            self.send_json(cached, headers={'X-Cache': 'HIT'})
            return
        data = get_quotes(list(tickers))
        _quote_cache.set(key, data)
        self.send_json(data, headers={'X-Cache': 'MISS'})
    
    def handle_news_top(self, qs):
        import news
        cat = qs.get('cat', ['general'])[0]
        action = qs.get('action', [None])[0]
        if action == 'calendar':
            self.send_json({'dates': news.list_news_dates()})
            return
        self.send_json(news.get_top_news(cat))
    
    def handle_news_cn(self, qs):
        import news
        self.send_json(news.get_cn_news())
    
    def handle_sentiment(self, qs):
        import sentiment
        scope = qs.get('scope', [None])[0]
        ticker = qs.get('ticker', [None])[0]
        metric = qs.get('metric', [None])[0]
        days = int(qs.get('days', [None])[0]) if qs.get('days', [None])[0] else None
        latest = qs.get('latest', ['0'])[0] in ('1', 'true', 'True')
        sources = qs.get('sources', [None])[0]
        sources = [s.strip() for s in sources.split(',')] if sources else None
        self.send_json(sentiment.get_sentiment(scope=scope, ticker=ticker, metric=metric,
                                               days=days, sources=sources, latest=latest))
    
    def handle_sentiment_metrics(self, qs):
        import sentiment
        self.send_json(sentiment.get_metrics())
    
    def handle_sentiment_providers(self, qs):
        import sentiment
        self.send_json(sentiment.list_providers())
    
    def handle_sentiment_ticker(self, qs):
        import sentiment
        ticker = (qs.get('ticker', [''])[0] or '').upper()
        self.send_json(sentiment.get_ticker_sentiment(ticker))
    
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

    def handle_screen_v2(self, qs):
        """Universe unusual-activity scan (cached; force=1 rebuilds; provider= feeds toggle)."""
        import option_screener
        force = qs.get('force', [''])[0] == '1'
        provider = qs.get('provider', [None])[0] or None
        self.send_json(option_screener.scan_universe(force=force, provider=provider))

    def handle_screen_ticker(self, qs):
        """Fresh per-ticker scored drilldown."""
        import option_screener
        ticker = qs.get('ticker', ['SPY'])[0].upper()
        provider = qs.get('provider', [None])[0] or None
        self.send_json(option_screener.scan_ticker(ticker, provider=provider))

    def handle_screen_status(self, qs):
        """Poll target for async (rate-limited) universe scans."""
        import option_screener
        provider = qs.get('provider', [None])[0] or None
        self.send_json(option_screener.scan_status(provider=provider))

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
        ticker = (qs.get('ticker') or [''])[0].strip().upper()
        if not ticker:
            self.send_json({"error": "ticker parameter required"}, status=400)
            return
        self.send_json(estimates.get_estimates(ticker))
    
    def handle_ratio(self, qs):
        t1, t2 = qs.get('t1', ['XLE'])[0], qs.get('t2', ['SPY'])[0]
        tf, sma_p = qs.get('tf', ['1Y'])[0], int(qs.get('sma', ['20'])[0])
        self.send_json(self.get_ratio_data(t1, t2, tf, sma_p))

    def handle_year_highs(self, qs):
        """52-week-high snapshots.

        GET /api/year-highs                          -> latest stored snapshot
        GET /api/year-highs?date=YYYY-MM-DD          -> snapshot for date
        GET /api/year-highs?action=store             -> store today if not saved
        GET /api/year-highs?action=calendar          -> list of stored dates
        GET /api/year-highs?action=search&q=AAPL     -> search latest snapshot
        """
        import db
        import year_highs
        action = qs.get('action', [None])[0]
        date_str = qs.get('date', [None])[0]

        if action == 'store':
            d, count, existed = year_highs.store_today_snapshot()
            self.send_json({'status': 'stored' if not existed else 'exists',
                            'date': d, 'count': count, 'already_existed': existed})
            return

        if action == 'calendar':
            self.send_json({'dates': db.list_dates()})
            return

        if action == 'search':
            q = qs.get('q', [''])[0]
            target = date_str or db.today_est_str()
            self.send_json({'date': target, 'query': q, 'results': db.search_year_highs(target, q)})
            return

        # default: return a snapshot
        target = date_str or db.today_est_str()
        rows = db.get_year_highs(target)
        if not rows and not date_str:
            dates = db.list_dates()
            if dates:
                target = dates[0]
                rows = db.get_year_highs(target)
        self.send_json({'date': target, 'count': len(rows), 'results': rows})
    
    def handle_year_lows(self, qs):
        """52-week-low snapshots.

        GET /api/year-lows                          -> latest stored snapshot
        GET /api/year-lows?date=YYYY-MM-DD          -> snapshot for date
        GET /api/year-lows?action=store             -> store today if not saved
        GET /api/year-lows?action=calendar          -> list of stored dates
        GET /api/year-lows?action=search&q=AAPL     -> search latest snapshot
        """
        import db
        import year_lows
        action = qs.get('action', [None])[0]
        date_str = qs.get('date', [None])[0]

        if action == 'store':
            d, count, existed = year_lows.store_today_snapshot()
            self.send_json({'status': 'stored' if not existed else 'exists',
                            'date': d, 'count': count, 'already_existed': existed})
            return

        if action == 'calendar':
            self.send_json({'dates': db.list_lows_dates()})
            return

        if action == 'search':
            q = qs.get('q', [''])[0]
            target = date_str or db.today_est_str()
            self.send_json({'date': target, 'query': q, 'results': db.search_year_lows(target, q)})
            return

        # default: return a snapshot
        target = date_str or db.today_est_str()
        rows = db.get_year_lows(target)
        if not rows and not date_str:
            dates = db.list_lows_dates()
            if dates:
                target = dates[0]
                rows = db.get_year_lows(target)
        self.send_json({'date': target, 'count': len(rows), 'results': rows})

    def handle_year_highs_trend(self, qs):
        """Per-date sector counts for the 52W-high trend chart."""
        import year_highs
        self.send_json({'results': year_highs.get_trend()})

    def handle_year_lows_trend(self, qs):
        """Per-date sector counts for the 52W-low trend chart."""
        import year_lows
        self.send_json({'results': year_lows.get_trend()})

    def handle_macro(self, qs):
        """Macro economics page payload (6 categories, FRED + computed series)."""
        import macro
        self.send_json(macro.get_macro())
    
    def handle_sec_financials(self, qs):
        import sec_financials
        action = qs.get('action', [None])[0]
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
        
        import sec_financials
        ticker = (qs.get('ticker', ['SPY'])[0] or '').strip().upper()
        periods = int(qs.get('periods', [8])[0])
        period_type = qs.get('type', ['Q'])[0]
        data = sec_financials.fetch_financials(ticker, periods, period_type)

        # SEC path empty (ADR / foreign issuer, or XBRL gap) -> Yahoo fallback.
        # Build a FRESH payload: the old code mutated the SEC dict in place,
        # leaving `error` set, so the UI hard-errored despite having data.
        if not data.get('income'):
            import yahoo_financials
            import fundamentals
            y = yahoo_financials.get_financials(ticker, periods, period_type)
            if y.get('income'):
                metrics = fundamentals.calculate_graham_metrics(
                    y['income'], y['balance'], y['cashflow'],
                    y.get('info') or {}, ticker)
                data = {
                    'ticker': ticker,
                    'source': 'Yahoo Finance (ADR/Fallback)',
                    'income': y['income'],
                    'balance': y['balance'],
                    'cashflow': y['cashflow'],
                    'metrics': metrics,
                    'info': y.get('info') or {},
                }
            else:
                data = {
                    'ticker': ticker,
                    'error': data.get('error') or
                             f'No financial data available for {ticker}',
                    'source': 'SEC EDGAR (XBRL)',
                    'income': [], 'balance': [], 'cashflow': [],
                    'metrics': {},
                }
        self.send_json(data)
    
    def handle_fundamentals_screen(self, qs):
        import fundamental_screener
        force = qs.get('force', ['0'])[0] in ('1', 'true', 'True')
        as_of = qs.get('as_of', [None])[0]
        ticker = qs.get('ticker', [None])[0]
        rows = fundamental_screener.screen_universe(as_of=as_of, force=force)
        if ticker:
            ticker = ticker.strip().upper()
            rows = [r for r in rows if r['ticker'] == ticker]
        self.send_json({'count': len(rows), 'results': rows})

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

class QuietThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that treats routine client aborts (browser closing
    mid-response) as non-errors — no traceback spam in the logs."""
    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


class AlphaTerminalServer:
    def __init__(self, host=HOST, port=PORT):
        self.host = host
        self.port = port
        self.server = None
        self.server_thread = None
        self._shutdown = threading.Event()
    
    def start(self):
        """Start the HTTP server in a background thread."""
        Handler._discover_module_routes()
        self.server = QuietThreadingHTTPServer((self.host, self.port), Handler)
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