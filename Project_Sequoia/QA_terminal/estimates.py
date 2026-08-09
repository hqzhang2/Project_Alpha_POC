"""
Earnings Estimates Module (Bloomberg EE style).

Consensus summary, earnings history/surprise, EPS revision trends + momentum,
next earnings date, analyst price targets/ratings, growth vs benchmark, and
surprise quality stats — all from Yahoo Finance.

Design notes:
  - yfinance imported lazily: this module is imported at server startup by
    R2 route discovery (py3.9 CLT server), so no heavy imports at module
    level. Tests swap `_yf` for a fake.
  - Fail-open per attribute: a dead endpoint degrades that section only;
    catastrophic failures return {"error": ...} (never cached).
  - NaN/inf scrubbed to None at the boundary. Browsers reject bare `NaN`
    JSON literals (regression: GME yearAgoEps NaN blanked the whole page).
  - Hourly TTL cache keyed by ticker (4+ Yahoo calls per page load
    otherwise).
  - R2: ROUTES = {'/api/estimates': 'handle_estimates'}; handler method on
    Handler in server.py; module registered in _discover_module_routes().
"""
import math
import time

CACHE_TTL = 3600  # seconds; consensus estimates move slowly

_cache = {}  # ticker -> (fetch_time, payload)

# yfinance module, imported on first use; tests monkeypatch this to a fake.
_yf = None

ROUTES = {'/api/estimates': 'handle_estimates'}


def _yf_module():
    global _yf
    if _yf is None:
        import yfinance
        _yf = yfinance
    return _yf


# --------------------------------------------------------------------------- #
# JSON-safe value helpers
# --------------------------------------------------------------------------- #
def _num(v):
    """JSON-safe scalar: NaN/inf -> None; integral floats -> int."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    if math.isnan(f) or math.isinf(f):
        return None
    return int(f) if f.is_integer() else f


def _iso(v):
    """Timestamp/date -> ISO string (None-safe), else pass through."""
    if v is None:
        return None
    if hasattr(v, 'isoformat'):
        try:
            return v.isoformat()
        except (TypeError, ValueError):
            return str(v)
    return v


def _col(df, name):
    """Resolve a column name case-insensitively (yfinance renames columns
    between releases, e.g. 'downLast7Days' vs 'downLast7days')."""
    if df is None:
        return None
    lower = name.lower()
    for c in df.columns:
        if str(c).lower() == lower:
            return c
    return None


def _cell(df, idx, col):
    """Read one cell defensively: missing df / index / column -> None."""
    if df is None or idx not in df.index or col not in df.columns:
        return None
    return _num(df.loc[idx, col])


def _attr(t, name):
    """Call a yfinance attribute, failing open (404s, network, bad symbol)."""
    try:
        return getattr(t, name)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Section builders (each takes the raw yfinance frame/dict, returns JSON-safe)
# --------------------------------------------------------------------------- #
def _summary_rows(ee, rev):
    """Consensus summary: EPS + revenue consensus per period."""
    if ee is None or ee.empty:
        return []
    rows = []
    for period in ee.index:
        rows.append({
            "period": period,
            "eps_avg": _cell(ee, period, 'avg'),
            "eps_low": _cell(ee, period, 'low'),
            "eps_high": _cell(ee, period, 'high'),
            "eps_year_ago": _cell(ee, period, 'yearAgoEps'),
            "eps_growth": _cell(ee, period, 'growth'),
            "analysts": _cell(ee, period, 'numberOfAnalysts'),
            "rev_avg": _cell(rev, period, 'avg'),
            "rev_low": _cell(rev, period, 'low'),
            "rev_high": _cell(rev, period, 'high'),
            "rev_growth": _cell(rev, period, 'growth'),
        })
    return rows


def _history_rows(hist):
    """Earnings history + surprise per quarter."""
    if hist is None or hist.empty:
        return []
    rows = []
    for q in hist.index:
        rows.append({
            "quarter": _iso(q),
            "actual": _cell(hist, q, 'epsActual'),
            "estimate": _cell(hist, q, 'epsEstimate'),
            "surprise": _cell(hist, q, 'surprisePercent'),
        })
    return rows


def _trend_rows(trend):
    """EPS consensus trend: current vs 7/30/90 days ago."""
    if trend is None or trend.empty:
        return []
    rows = []
    for period in trend.index:
        rows.append({
            "period": period,
            "current": _cell(trend, period, 'current'),
            "7days": _cell(trend, period, '7daysAgo'),
            "30days": _cell(trend, period, '30daysAgo'),
            "90days": _cell(trend, period, '90daysAgo'),
        })
    return rows


def _revision_rows(rev_df):
    """eps_revisions: up/down analyst revisions over 7d/30d, per period."""
    if rev_df is None or rev_df.empty:
        return []
    up7 = _col(rev_df, 'upLast7days')
    up30 = _col(rev_df, 'upLast30days')
    down30 = _col(rev_df, 'downLast30days')
    down7 = _col(rev_df, 'downLast7Days')
    rows = []
    for period in rev_df.index:
        u7, u30 = _cell(rev_df, period, up7), _cell(rev_df, period, up30)
        d30, d7 = _cell(rev_df, period, down30), _cell(rev_df, period, down7)
        net = u30 - d30 if (u30 is not None and d30 is not None) else None
        rows.append({
            "period": period,
            "up_7d": u7,
            "up_30d": u30,
            "down_30d": d30,
            "down_7d": d7,
            "net_30d": net,
        })
    return rows


def _next_earnings(t):
    """Upcoming earnings date/time + consensus EPS estimate (first row of
    earnings_dates is the next report; Reported EPS is absent until then)."""
    ed = _attr(t, 'earnings_dates')
    if ed is None or ed.empty:
        return None
    idx = ed.index[0]
    return {
        "date": _iso(idx),
        "eps_estimate": _cell(ed, idx, 'EPS Estimate'),
        "reported_eps": _cell(ed, idx, 'Reported EPS'),
        "surprise_pct": _cell(ed, idx, 'Surprise(%)'),
    }


def _price_targets(t):
    apt = _attr(t, 'analyst_price_targets')
    if not isinstance(apt, dict):
        return None
    return {k: _num(v) for k, v in apt.items()}


def _recommendations(t):
    """Analyst rating counts per month (0m = current)."""
    rec = _attr(t, 'recommendations_summary')
    if rec is None or rec.empty:
        return None
    rows = []
    for i in rec.index:
        rows.append({
            "period": _cell(rec, i, 'period'),
            "strong_buy": _cell(rec, i, 'strongBuy'),
            "buy": _cell(rec, i, 'buy'),
            "hold": _cell(rec, i, 'hold'),
            "sell": _cell(rec, i, 'sell'),
            "strong_sell": _cell(rec, i, 'strongSell'),
        })
    return rows


def _growth_rows(t):
    """EPS growth trend vs index/benchmark, incl. LTG (long-term growth)."""
    g = _attr(t, 'growth_estimates')
    if g is None or g.empty:
        return None
    stock = _col(g, 'stockTrend')
    index = _col(g, 'indexTrend')
    rows = []
    for period in g.index:
        rows.append({
            "period": period,
            "stock": _cell(g, period, stock),
            "index": _cell(g, period, index),
        })
    return rows


def _surprise_stats(history):
    """Beat rate / avg surprise / streak derived from the surprise table."""
    chrono = sorted(history, key=lambda h: str(h.get('quarter')))
    surps = [h['surprise'] for h in chrono if h.get('surprise') is not None]
    if not surps:
        return None
    beats = sum(1 for s in surps if s > 0)
    streak = 0
    for s in reversed(surps):  # most recent last
        if s > 0:
            streak += 1
        else:
            break
    return {
        "n": len(surps),
        "beat_rate": beats / len(surps),
        "avg_surprise_pct": sum(surps) / len(surps) * 100,
        "best_pct": max(surps) * 100,
        "worst_pct": min(surps) * 100,
        "beat_streak": streak,
    }


# --------------------------------------------------------------------------- #
# Fetch + cache
# --------------------------------------------------------------------------- #
def _fetch_estimates(ticker):
    yf = _yf_module()
    t = yf.Ticker(ticker)
    ee = _attr(t, 'earnings_estimate')
    rev = _attr(t, 'revenue_estimate')
    hist = _attr(t, 'earnings_history')
    trend = _attr(t, 'eps_trend')

    history = _history_rows(hist)
    return {
        "ticker": ticker,
        "summary": _summary_rows(ee, rev),
        "history": history,
        "trends": _trend_rows(trend),
        "revisions": _revision_rows(_attr(t, 'eps_revisions')),
        "growth": _growth_rows(t),
        "recommendations": _recommendations(t),
        "price_targets": _price_targets(t),
        "next_earnings": _next_earnings(t),
        "surprise_stats": _surprise_stats(history),
    }


def get_estimates(ticker):
    """Consensus estimates payload for a ticker, TTL-cached per ticker.

    Errors are returned but never cached (fail-open: a transient Yahoo
    failure retries on the next request instead of serving stale errors).
    """
    key = (ticker or '').strip().upper()
    if not key:
        return {"error": "ticker required"}
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]
    try:
        payload = _fetch_estimates(key)
    except Exception as e:
        return {"error": str(e)}
    _cache[key] = (now, payload)
    return payload
