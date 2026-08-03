"""
Option Screener v2.4 — unusual-activity features + composite score + universe scan.

Replaces the old single-ticker dump (`/api/screen`). Signals target two scenarios:
  A) insider/advanced knowledge: pre-catalyst OTM call concentration
  B) institutional positioning: OI accumulation (via Phase-2 store), deep-OTM puts,
     big notional, "OI up + price flat" divergence.

Data access goes through options_data.get_provider() — never yfinance directly.
All JSON outputs use native types (str/int/float/None) — safe for json.dumps.
"""
import datetime
import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor

import config

# ---------------------------------------------------------------------------
# caches (module-level; server lifetime)
# ---------------------------------------------------------------------------
_scan_cache = {"data": None, "ts": 0.0}
_universe_cache = {"names": None, "ts": 0.0}
_earnings_cache = {"data": {}, "ts": 0.0}


# ---------------------------------------------------------------------------
# pure feature helpers
# ---------------------------------------------------------------------------
def _zscore(vals):
    """Standardized list; guards zero-variance / short input."""
    vals = [float(v) for v in vals]
    n = len(vals)
    if n < 2:
        return [0.0] * n
    m = sum(vals) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in vals) / n)
    if sd < 1e-9:
        return [0.0] * n
    return [(x - m) / sd for x in vals]


def moneyness_mult(strike, spot, opt_type):
    """1.0 (ATM/ITM) .. 2.0 (>20% OTM). Depth-of-OTM matters for both scenarios."""
    if not strike or not spot or spot <= 0:
        return 1.0
    pct_otm = max(0.0, (strike - spot) / spot if opt_type == "Call" else (spot - strike) / spot)
    if pct_otm >= 0.20:
        return 2.0
    if pct_otm >= 0.10:
        return 1.6
    if pct_otm >= 0.05:
        return 1.3
    return 1.0


def dte_of(expiry):
    try:
        y, m, d = (int(x) for x in expiry.split("-"))
        return (datetime.date(y, m, d) - datetime.date.today()).days
    except Exception:
        return 999


def catalyst_bonus(days_to_earnings):
    if days_to_earnings is None:
        return 0.0
    if days_to_earnings <= 7:
        return 1.0
    if days_to_earnings <= config.SCREENER_EARNINGS_WINDOW_DAYS:
        return 0.5
    return 0.0


def iv_cheap_flag(record, ctx_ivs):
    """1.0 when record IV is below the ticker-side median (same option type)."""
    iv = record.get("iv")
    if not iv or not ctx_ivs:
        return 0.0
    med = sorted(ctx_ivs)[len(ctx_ivs) // 2]
    return 1.0 if iv < med else 0.0


def score_contract(r):
    w = config.SCORE_WEIGHTS_OI if r.get("oi_build_z") is not None else config.SCORE_WEIGHTS
    s = (w["vol_oi_z"] * r.get("vol_oi_z", 0.0)
         + w["notional_z"] * r.get("notional_z", 0.0)
         + w["moneyness"] * (r.get("moneyness_mult", 1.0) - 1.0)
         + w["iv_cheap"] * r.get("iv_cheap", 0.0)
         + w["catalyst"] * r.get("catalyst_bonus", 0.0))
    if r.get("oi_build_z") is not None:
        s += w["oi_build_z"] * r["oi_build_z"]
    if r.get("dte", 999) <= config.SCREENER_MIN_DTE:
        s *= 0.3  # dampen 0DTE/1DTE (index casino flow) unless user lifts DTE filter
    return round(s, 2)


def tier_of(score):
    if score >= config.SCORE_TIER_HIGH:
        return "HIGH"
    if score >= config.SCORE_TIER_MED:
        return "MED"
    return "LOW"


def _mid(rec):
    b, a, l = rec.get("bid"), rec.get("ask"), rec.get("last")
    if b and a and b > 0 and a > 0:
        return (b + a) / 2.0
    return l or 0.0


# ---------------------------------------------------------------------------
# enrichment + scoring
# ---------------------------------------------------------------------------
def enrich_ticker_contracts(records, spot, earnings_date):
    """Add per-contract features + cross-section z-scores (this ticker's set)."""
    today = datetime.date.today()
    for r in records:
        r["notional"] = round((r.get("vol") or 0) * _mid(r) * 100, 0)
        r["vol_oi"] = (r.get("vol") or 0) / max(r.get("oi") or 0, 1)
        strike, spot_s, opt_type = r.get("strike"), spot, r.get("type", "Call")
        pct_otm = 0.0
        if strike and spot_s and spot_s > 0:
            pct_otm = max(0.0, (strike - spot_s) / spot_s if opt_type == "Call" else (spot_s - strike) / spot_s)
        r["otm_pct"] = round(pct_otm * 100, 1)          # 0 for ATM/ITM
        r["moneyness_mult"] = moneyness_mult(strike, spot_s, opt_type)
        r["dte"] = dte_of(r.get("expiry", ""))
        if earnings_date:
            try:
                d = datetime.date.fromisoformat(earnings_date)
                r["catalyst_bonus"] = catalyst_bonus((d - today).days)
            except Exception:
                r["catalyst_bonus"] = 0.0
        else:
            r["catalyst_bonus"] = 0.0
    vol_oi_log = _zscore([math.log1p(r["vol_oi"]) for r in records])
    notional_log = _zscore([math.log1p(max(r["notional"], 0)) for r in records])
    side_ivs = {"Call": [r["iv"] for r in records if r.get("type") == "Call" and r.get("iv")],
                "Put": [r["iv"] for r in records if r.get("type") == "Put" and r.get("iv")]}
    for i, r in enumerate(records):
        r["vol_oi_z"] = round(vol_oi_log[i], 2)
        r["notional_z"] = round(notional_log[i], 2)
        r["iv_cheap"] = iv_cheap_flag(r, side_ivs.get(r.get("type", "Call"), []))
        r["score"] = score_contract(r)
        r["tier"] = tier_of(r["score"])
    return records


def _attach_oi_signals(ticker, records):
    """Phase-2 store signals per contract: OI build % (1/5/20d), vol percentile,
    OI-up-price-flat divergence + cross-section oi_build_z. Fail-open: no history
    (or store missing) -> fields absent -> score falls back to base weights."""
    try:
        import option_oi_store
        hist_map, spots = option_oi_store.load_ticker_history(ticker)
        if not hist_map:
            return
        for r in records:
            h = hist_map.get((r.get("expiry"), r.get("strike"), r.get("type")))
            if not h:
                continue
            sig = option_oi_store.build_signals(h, spots)
            if not sig:
                continue
            for k, v in sig.items():
                r[k] = round(v, 4) if isinstance(v, float) else v
        builds = [r.get("oi_build_5d") for r in records]
        if any(b is not None for b in builds):
            z = _zscore([b if b is not None else 0.0 for b in builds])
            for i, r in enumerate(records):
                if r.get("oi_build_5d") is not None:
                    r["oi_build_z"] = round(z[i], 2)
    except Exception:
        return  # store unavailable -> fail open


# ---------------------------------------------------------------------------
# universe + scan
# ---------------------------------------------------------------------------
def _watchlist():
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchlist.json")
        if os.path.exists(p):
            with open(p) as f:
                return json.load(f)
    except Exception:
        pass
    return list(config.SCREENER_WATCHLIST)


def _today_plus(days):
    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()


def _market_cap(ticker):
    """Lazy yfinance info call — ONLY for earnings-window candidates."""
    try:
        import yfinance
        return yfinance.Ticker(ticker).info.get("marketCap")
    except Exception:
        return None


def _universe(provider):
    """watchlist + liquid pool + earnings names (gated by $100B mcap), capped.

    Two-pass: the heavy `info` (marketCap) call only runs for the few candidates
    with earnings inside the window — not all base names. Cached 24h."""
    if _universe_cache["names"] and time.time() - _universe_cache["ts"] < config.SCREENER_EARNINGS_CACHE_TTL:
        return _universe_cache["names"], _earnings_cache["data"]
    names = list(dict.fromkeys(_watchlist() + list(config.SCREENER_LIQUID_POOL)))
    with ThreadPoolExecutor(max_workers=config.SCREENER_MAX_WORKERS) as ex:
        earnings = dict(zip(names, ex.map(provider.get_next_earnings, names)))
    soon = [t for t, e in earnings.items()
            if e and e <= _today_plus(config.SCREENER_EARNINGS_WINDOW_DAYS) and t not in _watchlist()]
    mcaps = {}
    if soon:
        with ThreadPoolExecutor(max_workers=config.SCREENER_MAX_WORKERS) as ex:
            mcaps = dict(zip(soon, ex.map(_market_cap, soon)))
    extra = [t for t in soon if (mcaps.get(t) or 0) >= config.SCREENER_EARNINGS_MIN_MCAP]
    uni = names + [t for t in extra if t not in names]
    uni = uni[: config.SCREENER_MAX_UNIVERSE]
    _earnings_cache["data"] = earnings
    _earnings_cache["ts"] = time.time()
    _universe_cache["names"] = uni
    _universe_cache["ts"] = time.time()
    return uni, earnings


def _scan_ticker(provider, ticker):
    try:
        expiries = provider.get_expirations(ticker)[: config.SCREENER_MAX_EXPIRIES]
        chains = [provider.get_chain(ticker, e) for e in expiries]
        calls, puts, spot = [], [], None
        for c in chains:
            if "error" in c:
                continue
            spot = c.get("spot") or spot
            exp = c.get("expiry")
            for r in c.get("calls", []):
                r.update(type="Call", expiry=exp)
                calls.append(r)
            for r in c.get("puts", []):
                r.update(type="Put", expiry=exp)
                puts.append(r)
        if not calls and not puts:
            return None
        records = enrich_ticker_contracts(calls + puts, spot, _earnings_cache["data"].get(ticker))
        _attach_oi_signals(ticker, records)          # Phase-2 store signals (fail-open)
        for r in records:                            # re-score with OI weights when history exists
            r["score"] = score_contract(r)
            r["tier"] = tier_of(r["score"])
        scored = [r for r in records if r["score"] > 0 and (r.get("otm_pct") or 0) > 0]  # OTM only
        scored.sort(key=lambda r: r["score"], reverse=True)
        total = sum(r["notional"] for r in records)
        cp = sum(r["notional"] for r in records if r["type"] == "Call")
        pp = sum(r["notional"] for r in records if r["type"] == "Put")
        top = scored[0] if scored else None
        summary = {
            "ticker": ticker,
            "spot": round(spot, 2) if spot else None,
            "total_premium": round(total),
            "call_premium": round(cp),
            "put_premium": round(pp),
            "pc_ratio": round(cp / pp, 2) if pp > 0 else None,
            "unusual_count": sum(1 for r in scored if r["tier"] in ("HIGH", "MED")),
            "max_score": top["score"] if top else None,
            "top_hit": (f"{top['expiry']} {top['type']} K={top['strike']:.1f} ${_mid(top):.2f}"
                        f" vol={top.get('vol') or 0} oi={top.get('oi') or 0} score={top['score']}") if top else None,
            "catalyst": _earnings_cache["data"].get(ticker),
            "contracts": [{k: r.get(k) for k in ("expiry", "strike", "type", "last", "vol", "oi", "iv",
                                                  "notional", "dte", "otm_pct", "moneyness_mult", "vol_oi_z",
                                                  "notional_z", "iv_cheap", "oi_build_5d", "vol_pctile",
                                                  "divergence", "oi_build_z", "score", "tier")}
                           for r in scored[:20]],
        }
        return summary
    except Exception:
        return None


def scan_universe(force=False):
    """Cached universe scan: {cached_at, count, tickers:[summary...]}. Thread-pooled."""
    if not force and _scan_cache["data"] and time.time() - _scan_cache["ts"] < config.SCREENER_CACHE_TTL:
        return _scan_cache["data"]
    provider = __import__("options_data").get_provider()
    uni, _ = _universe(provider)
    with ThreadPoolExecutor(max_workers=config.SCREENER_MAX_WORKERS) as ex:
        tickers = [t for t in ex.map(lambda t: _scan_ticker(provider, t), uni) if t]
    tickers.sort(key=lambda t: (t["max_score"] or 0), reverse=True)
    result = {"cached_at": time.strftime("%H:%M:%S"), "count": len(tickers), "tickers": tickers}
    _scan_cache["data"] = result
    _scan_cache["ts"] = time.time()
    return result


def scan_ticker(ticker, force=False):
    """Fresh per-ticker drilldown (uncached by design)."""
    provider = __import__("options_data").get_provider()
    _universe(provider)  # ensure earnings cache populated
    t = ticker.upper()
    if t not in _earnings_cache["data"]:
        # drilldown on a name outside the scan universe -> fetch its catalyst directly
        _earnings_cache["data"][t] = provider.get_next_earnings(t)
    return _scan_ticker(provider, t)


# Module route registration (R2) — handler methods live on the Handler class in server.py
ROUTES = {
    '/api/screen/v2': 'handle_screen_v2',
    '/api/screen/ticker': 'handle_screen_ticker',
}
