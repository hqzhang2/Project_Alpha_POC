"""
Polygon.io options provider (free tier, delayed data) — v2.4 data-layer toggle.

Polygon = pure data vendor (API key only, no broker account/holdings/trading
surface). Free tier: ~5 req/min, delayed quotes — fine for the screener's
stock-trading support (not time-sensitive).

One `GET /v3/snapshot/options/{underlying}` returns the WHOLE chain (all
expiries/strikes) with day volume, open_interest, bid/ask, implied vol,
greeks and the underlying spot — that single call drives the entire screener.
Rate-limited to config.POLYGON_RATE_PER_MIN; per-underlying snapshot cached
config.POLYGON_CHAIN_TTL so repeated get_chain() calls don't re-hit the API.

Record shape matches options.get_options_chain's (ticker/expiry/spot/calls/puts)
so option_screener consumes it unchanged. Earnings delegate to yfinance (free).
"""
import json
import os
import threading
import time
import urllib.parse
import urllib.request

import config


class RateLimited:
    """Min-interval throttle (thread-safe). interval_s = 60 / calls_per_min."""

    def __init__(self, calls_per_min):
        self._interval = 60.0 / max(calls_per_min, 1)
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self):
        with self._lock:
            now = time.time()
            if now < self._next_at:
                time.sleep(self._next_at - now)
            self._next_at = time.time() + self._interval


_rate = RateLimited(config.POLYGON_RATE_PER_MIN)
_snap_cache = {}            # ticker -> (ts, data)
_snap_lock = threading.Lock()


def _api_key():
    return os.environ.get(config.POLYGON_API_KEY_ENV) or None


def _get(url):
    """Rate-limited GET returning parsed JSON. Raises RuntimeError on API errors."""
    key = _api_key()
    if not key:
        raise RuntimeError(f"{config.POLYGON_API_KEY_ENV} not set")
    _rate.wait()
    sep = "&" if "?" in url else "?"
    with urllib.request.urlopen(f"{url}{sep}apiKey={urllib.parse.quote(key)}", timeout=30) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    if data.get("status") == "ERROR" or data.get("error"):
        raise RuntimeError(f"polygon: {data.get('error') or data.get('message', 'api error')}")
    return data


def _snapshot(ticker):
    """Whole-chain snapshot for one underlying, cached POLYGON_CHAIN_TTL."""
    key = ticker.upper()
    now = time.time()
    with _snap_lock:
        hit = _snap_cache.get(key)
        if hit and now - hit[0] < config.POLYGON_CHAIN_TTL:
            return hit[1]
    data = _get(f"{config.POLYGON_BASE}/v3/snapshot/options/{urllib.parse.quote(key)}")
    with _snap_lock:
        _snap_cache[key] = (now, data)
    return data


def _records(data, expiry):
    """Map polygon snapshot contracts -> screener record shape."""
    calls, puts = [], []
    spot = None
    for c in data.get("results", []) or []:
        det = c.get("details") or {}
        if det.get("expiration_date") != expiry:
            continue
        typ = "Call" if str(det.get("contract_type", "")).lower() == "call" else "Put"
        q = c.get("last_quote") or {}
        day = c.get("day") or {}
        rec = {
            "strike": det.get("strike_price"),
            "vol": day.get("volume") or 0,
            "oi": c.get("open_interest") or 0,
            "bid": q.get("bid"),
            "ask": q.get("ask"),
            "last": day.get("close") or q.get("mid"),
            "iv": c.get("implied_volatility"),
        }
        (calls if typ == "Call" else puts).append(rec)
    ua = data.get("results") and (data["results"][0].get("underlying_asset") or {})
    spot = ua.get("price")
    return calls, puts, spot


class PolygonProvider:
    name = "polygon"
    IMPLEMENTED = True
    API_KEY_ENV = config.POLYGON_API_KEY_ENV
    ASYNC_SCAN = True              # 5 req/min free tier -> universe scan takes ~8 min (background)
    UNAVAILABLE_REASON = f"{config.POLYGON_API_KEY_ENV} env var not set"

    def get_expirations(self, ticker):
        data = _snapshot(ticker)
        exps = sorted({(c.get("details") or {}).get("expiration_date")
                       for c in data.get("results", []) or [] if c.get("details")})
        return exps

    def get_chain(self, ticker, expiry=None):
        try:
            data = _snapshot(ticker)
            if not data.get("results"):
                return {"ticker": ticker.upper(), "expiry": expiry,
                        "spot": None, "calls": [], "puts": [], "timestamp": time.time()}
            if not expiry:
                expiry = sorted({(c.get("details") or {}).get("expiration_date")
                                 for c in data["results"] if c.get("details")})[0]
            calls, puts, spot = _records(data, expiry)
            return {"ticker": ticker.upper(), "expiry": expiry, "spot": spot,
                    "calls": calls, "puts": puts, "timestamp": time.time()}
        except Exception as e:
            return {"ticker": ticker.upper(), "expiry": expiry, "error": str(e)}

    def get_next_earnings(self, ticker):
        try:
            from options_data import YFinanceProvider
            return YFinanceProvider().get_next_earnings(ticker)
        except Exception:
            return None

    def get_underlying_oi_history(self, ticker):
        return None  # our own OI store accumulates this (warm-up applies)
