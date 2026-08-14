"""
options_feed.py — NS-6 live options chain fetch (G3).

Fetches A_T's /api/options endpoint (server-side) and extracts the ATM put
mid premium that `options.recommend_put_overlay` uses as its monthly cost.
Fail-open: any fetch/parse error -> None, and the caller falls back to the
VIX parametric proxy with `pricing_source="proxy"`.

Design:
- TTL cache (default 1h) — the dashboard polls /api/enforcement/status every
  15s; an options chain changes slowly and A_T's endpoint forces a fresh
  yfinance fetch, so we must not hammer it per poll.
- Unit convention matches `options.estimate_put_cost_pct`: FRACTIONS
  (0.01 = 1% of notional). The chain's mid = (bid+ask)/2, else last trade.
- ATM = strike nearest `spot` among options with a usable price.
"""

import json
import logging
import os
import time
import urllib.request
from typing import Dict, Optional

log = logging.getLogger("ns6.options_feed")

AT_OPTIONS_PORT = 9099 if os.environ.get("ENV", "QA") == "QA" else 9098
AT_OPTIONS_URL = f"http://localhost:{AT_OPTIONS_PORT}/api/options"

CACHE_TTL_SECONDS = 3600  # 1h — chains move slowly; advisory pricing is daily
_cache: Dict[tuple, tuple] = {}  # key -> (timestamp, chain)


def fetch_chain(ticker: str = "SPY", expiry: Optional[str] = None,
                base_url: Optional[str] = None, use_cache: bool = True) -> Optional[Dict]:
    """Fetch the A_T options chain dict for ticker, or None (fail-open).

    The chain has {ticker, expiry, spot, calls: [...], puts: [...]}; each
    option record has strike/bid/ask/last/delta/... or the dict is
    {ticker, error} when no options are available.
    """
    key = (ticker, expiry, base_url)
    now = time.time()
    if use_cache and key in _cache:
        ts, data = _cache[key]
        if now - ts < CACHE_TTL_SECONDS:
            return data
    url = f"{base_url or AT_OPTIONS_URL}?ticker={ticker}"
    if expiry:
        url += f"&expiry={expiry}"
    data = None
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001 — fail-open
        log.warning("options feed %s failed: %s", ticker, exc)
    if not isinstance(data, dict) or "error" in data:
        return None
    if use_cache:
        _cache[key] = (now, data)
    return data


def _mid(row: Dict) -> Optional[float]:
    """Best price for an option record: bid/ask mid, else last trade."""
    bid, ask, last = row.get("bid"), row.get("ask"), row.get("last")
    if bid and ask and bid > 0 and ask > 0:
        return (float(bid) + float(ask)) / 2.0
    if last and last > 0:
        return float(last)
    return None


def _atm_premium(chain: Optional[Dict], side: str) -> Optional[float]:
    """ATM mid premium as a FRACTION of spot for the given side (puts/calls).

    ATM = strike nearest spot among that side's options with a usable price.
    Returns None when the chain is unusable (fail-open).
    """
    if not chain:
        return None
    spot = chain.get("spot")
    opts = chain.get(side) or []
    if not spot or spot <= 0 or not opts:
        return None
    best = None  # (distance, mid, strike)
    for row in opts:
        strike = row.get("strike")
        mid = _mid(row)
        if strike is None or mid is None or mid <= 0:
            continue
        dist = abs(float(strike) - float(spot))
        if best is None or dist < best[0]:
            best = (dist, mid, float(strike))
    if not best:
        return None
    return round(best[1] / float(spot), 6)


def atm_put_premium(chain: Optional[Dict]) -> Optional[float]:
    """ATM put mid premium as a fraction of spot (0.01 = 1% monthly)."""
    return _atm_premium(chain, "puts")


def atm_call_premium(chain: Optional[Dict]) -> Optional[float]:
    """ATM call mid premium as a fraction of spot (covered-call income ref)."""
    return _atm_premium(chain, "calls")


def live_premiums(ticker: str = "SPY", expiry: Optional[str] = None,
                  base_url: Optional[str] = None) -> Dict:
    """{put_frac, call_frac, source} — live chain mids, else proxy fallback.

    source is "live" when the put premium came from a real chain, else
    "proxy" (chain unavailable — caller uses the VIX parametric estimate).
    """
    chain = fetch_chain(ticker, expiry, base_url)
    put_frac = atm_put_premium(chain)
    call_frac = atm_call_premium(chain)
    return {
        "put_frac": put_frac,
        "call_frac": call_frac,
        "source": "live" if put_frac is not None else "proxy",
    }
