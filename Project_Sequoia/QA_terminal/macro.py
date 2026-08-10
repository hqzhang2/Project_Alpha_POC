"""Macro economics data module for Alpha Terminal (display-only).

Macro page = 6 categories (Growth & Labor, Inflation, Monetary & Yield
Curve, Credit & Financial Conditions, External, Markets), each a set of
FRED time series. Plain data graphs — no sentiment, no NS-5 portfolio
grading (Hong scope, 2026-08-08). The Credit subtab additionally carries a
treasury yield curve panel (today/yesterday/1W + period-ago curves).

Design:
  - FRED v1 API, key from env only (FRED_API_KEY in QA/PROD plists).
  - Fail-open: missing key / API error -> [] per series, never a crash.
  - TTL cache per series (daily 1h, weekly 6h, monthly 24h, quarterly 48h)
    so the page is cheap on reload; computed stock-bond corr shares the
    daily TTL so reloads don't re-hit Yahoo.
  - Computed series: 2s10s spread, BAA-AAA spread, GDP QoQ annualized,
    stock-bond 60d corr.
  - R2: ROUTES = {'/api/macro': 'handle_macro'}; handler method on Handler
    class in server.py; module registered in _discover_module_routes().
"""

import calendar
import json
import logging
import os
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta

logger = logging.getLogger("alpha-terminal.macro")

FRED_API_KEY_ENV = "FRED_API_KEY"
FRED_OBS_URL = "https://api.stlouisfed.org/fred/series/observations"
# 2Y window + tail taper margin (charts slice client-side anyway)
LOOKBACK_DAYS = 800
# Per-cadence cache TTL (seconds)
TTL = {"Daily": 3600, "Weekly": 21600, "Monthly": 86400, "Quarterly": 172800}
DEFAULT_TTL = 3600

# ---------------------------------------------------------------------------
# Catalog — the six subtabs and their FRED series (approved 2026-08-08).
# units: display unit suffix. fmt: 'pct' -> y-axis %. computed: built in code.
# ---------------------------------------------------------------------------
GROUPS = [
    {"key": "growth", "name": "Growth & Labor", "items": [
        {"id": "GDPC1", "name": "Real GDP (QoQ ann.)", "unit": "%", "cadence": "Quarterly", "computed": "qoq_ann"},
        {"id": "UNRATE", "name": "Unemployment Rate", "unit": "%", "cadence": "Monthly"},
        {"id": "ICSA", "name": "Initial Jobless Claims", "unit": "K", "cadence": "Weekly", "unit_scale": 0.001},
        {"id": "CIVPART", "name": "Labor Force Participation", "unit": "%", "cadence": "Monthly"},
        {"id": "CES0500000003", "name": "Avg Hourly Earnings (YoY)", "unit": "%", "cadence": "Monthly", "units": "pc1"},
    ]},
    {"key": "inflation", "name": "Inflation", "items": [
        {"id": "CPIAUCSL", "name": "CPI (YoY)", "unit": "%", "cadence": "Monthly", "units": "pc1"},
        {"id": "PCEPILFE", "name": "Core PCE (YoY)", "unit": "%", "cadence": "Monthly", "units": "pc1"},
        {"id": "T5YIFR", "name": "5y5y Forward Breakeven", "unit": "%", "cadence": "Daily"},
    ]},
    {"key": "monetary", "name": "Monetary & Yield Curve", "items": [
        {"id": "FEDFUNDS", "name": "Fed Funds Effective", "unit": "%", "cadence": "Daily"},
        {"id": "DGS2", "name": "2Y Treasury", "unit": "%", "cadence": "Daily"},
        {"id": "DGS10", "name": "10Y Treasury", "unit": "%", "cadence": "Daily"},
        {"id": "DFII5", "name": "5Y TIPS Real Yield", "unit": "%", "cadence": "Daily"},
        {"id": "DGS10-DGS2", "name": "2s10s Spread", "unit": "bp", "cadence": "Daily", "computed": "spread",
         "from": ["DGS10", "DGS2"], "scale": 100},
    ]},
    {"key": "credit", "name": "Credit & Financial Conditions", "items": [
        {"id": "BAA10Y-AAA10Y", "name": "BAA−AAA Spread", "unit": "bp", "cadence": "Daily", "computed": "spread",
         "from": ["BAA10Y", "AAA10Y"], "scale": 100},
        {"id": "NFCI", "name": "Chicago Fed NFCI", "unit": "", "cadence": "Weekly"},
        {"id": "BAMLH0A0HYM2", "name": "High-Yield OAS", "unit": "bp", "cadence": "Daily", "unit_scale": 100},
    ]},
    {"key": "external", "name": "External (USD & Commodities)", "items": [
        {"id": "DTWEXBGS", "name": "Trade-Weighted USD", "unit": "", "cadence": "Daily"},
        {"id": "DCOILWTICO", "name": "WTI Crude Oil", "unit": "$", "cadence": "Daily"},
    ]},
    {"key": "markets", "name": "Markets", "items": [
        {"id": "VIXCLS", "name": "VIX", "unit": "", "cadence": "Daily"},
        {"id": "SPY-TLT-CORR", "name": "Stock–Bond 60d Corr", "unit": "", "cadence": "Daily", "computed": "corr",
         "from": ["SPY", "TLT"]},
    ]},
]
SERIES_BY_ID = {it["id"]: it for g in GROUPS for it in g["items"]}

# ---------------------------------------------------------------------------
# FRED fetch — stdlib urllib (py3.9-safe), fail-open, TTL cache.
# ---------------------------------------------------------------------------
_cache = {}
_lock = threading.Lock()


def _fred_key():
    return os.environ.get(FRED_API_KEY_ENV, "")


def _observations(series_id, start_date, units=None):
    """FRED observations [{date, value}] from start_date, oldest first."""
    key = _fred_key()
    if not key:
        logger.warning("FRED_API_KEY not set; macro series %s unavailable", series_id)
        return []
    url = (f"{FRED_OBS_URL}?series_id={series_id}&api_key={key}"
           f"&file_type=json&observation_start={start_date}&sort_order=asc")
    if units:
        url += f"&units={units}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        logger.warning("FRED fetch failed for %s: %s", series_id, e)
        return []
    out = []
    for obs in data.get("observations", []):
        try:
            v = float(obs["value"])
        except (TypeError, ValueError):
            continue
        out.append({"date": obs["date"], "value": v})
    return out


def get_series(series_id):
    """Cached observations for a raw FRED series (id + FRED units transform)."""
    item = SERIES_BY_ID.get(series_id, {})
    cache_key = series_id + ":" + item.get("units", "")
    with _lock:
        cached = _cache.get(cache_key)
        if cached and time.time() - cached[0] < TTL.get(item.get("cadence", ""), DEFAULT_TTL):
            return cached[1]
    start = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    obs = _observations(series_id, start, item.get("units"))
    with _lock:
        _cache[cache_key] = (time.time(), obs)
    return obs


def _spread(a_id, b_id, scale=100):
    """Point-in-time spread a-b * scale, aligned by date."""
    a, b = get_series(a_id), get_series(b_id)
    bmap = {o["date"]: o["value"] for o in b}
    out = []
    for o in a:
        if o["date"] in bmap:
            out.append({"date": o["date"], "value": round((o["value"] - bmap[o["date"]]) * scale, 2)})
    return out


CORR_CACHE_KEY = "SPY-TLT-CORR:corr"  # computed series, Daily TTL


def _corr_60d():
    """Stock–bond 60d rolling correlation from SPY + TLT daily returns.

    Cached like a raw FRED series (Daily TTL) so page reloads don't hit
    Yahoo's download endpoint every time.
    """
    with _lock:
        cached = _cache.get(CORR_CACHE_KEY)
        if cached and time.time() - cached[0] < TTL["Daily"]:
            return cached[1]
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance unavailable; stock-bond corr skipped")
        return []
    try:
        px = yf.download(["SPY", "TLT"], period="2y", interval="1d",
                         auto_adjust=False, progress=False)
        close = px["Close"].dropna()
        ret = close.pct_change().dropna()
        if len(ret) < 65:
            return []
        out = []
        for i in range(60, len(ret)):
            w = ret.iloc[i - 59:i + 1]
            corr = w["SPY"].corr(w["TLT"])
            if corr == corr:  # not NaN
                out.append({"date": str(ret.index[i])[:10], "value": round(corr, 3)})
        with _lock:
            _cache[CORR_CACHE_KEY] = (time.time(), out)
        return out
    except Exception as e:
        logger.warning("stock-bond corr failed (fail-open): %s", e)
        return []


def _qoq_ann(obs):
    """Quarterly level series -> QoQ annualized % change: (v_t/v_{t-1})^4 - 1."""
    out = []
    for i in range(1, len(obs)):
        prev, cur = obs[i - 1], obs[i]
        if prev["value"] and cur["value"]:
            out.append({"date": cur["date"],
                        "value": round(((cur["value"] / prev["value"]) ** 4 - 1) * 100, 2)})
    return out


def _item_payload(item):
    """Series observations with unit conversions for the catalog item."""
    if item.get("computed") == "spread":
        src = item["from"]
        return _spread(src[0], src[1], item.get("scale", 100))
    if item.get("computed") == "corr":
        return _corr_60d()
    if item.get("computed") == "qoq_ann":
        return _qoq_ann(get_series(item["id"]))
    obs = get_series(item["id"])
    if item.get("unit_scale"):  # e.g. ICSA claims -> K, HY OAS decimal -> bp
        return [{"date": o["date"], "value": round(o["value"] * item["unit_scale"], 2)} for o in obs]
    return obs


def get_macro():
    """Full payload for /api/macro: 6 groups, each item = series observations."""
    configured = bool(_fred_key())
    groups = []
    for g in GROUPS:
        items = []
        for it in g["items"]:
            items.append({
                "id": it["id"], "name": it["name"], "unit": it.get("unit", ""),
                "cadence": it.get("cadence", ""), "observations": _item_payload(it),
            })
        groups.append({"key": g["key"], "name": g["name"], "items": items})
    return {"generated": datetime.now().isoformat(timespec="seconds"),
            "configured": configured, "groups": groups,
            "yield_curve": get_yield_curve()}


# ---------------------------------------------------------------------------
# Treasury yield curve (Credit & Financial Conditions tab, approved 2026-08).
# Tenor ladder: FRED constant-maturity set, x-axis tenor in years (linear).
# Anchors: today / yesterday / 1W always; period-ago (1M..2Y, YTD) per the
# page's global period selector. Weekend/holiday rules (Hong):
#   - today = last available curve date <= last weekday <= today (Sat/Sun ->
#     Fri; if today's data is not yet published — FRED ingests ~6pm ET — the
#     most recent available curve is shown, honestly labeled with ITS date).
#     Updated 2026-08-10 (Hong): fall-back, not omit — a normal trading day
#     with a publication lag should not look "missing". The payload's per-
#     curve `date` field keeps the label honest.
#   - yesterday = last available curve date < today's resolved date; omitted
#     if no data.
#   - 1W / 1M / 3M / 6M / 1Y / 2Y: exact date offset, then FALL BACKWARD to
#     the last available curve (weekend -> previous Friday, holiday -> walk
#     back further). Always shown when any history exists.
#   - YTD: first available curve date in the current year.
# ---------------------------------------------------------------------------
TENORS = [
    ("1M", "DGS1MO", 1 / 12), ("3M", "DGS3MO", 0.25), ("6M", "DGS6MO", 0.5),
    ("1Y", "DGS1", 1.0), ("2Y", "DGS2", 2.0), ("3Y", "DGS3", 3.0),
    ("5Y", "DGS5", 5.0), ("7Y", "DGS7", 7.0), ("10Y", "DGS10", 10.0),
    ("20Y", "DGS20", 20.0), ("30Y", "DGS30", 30.0),
]


def _tenor_maps():
    """{sid: {date_str: yield}} for every tenor, computed once per request."""
    return {sid: {o["date"]: o["value"] for o in get_series(sid)} for _, sid, _y in TENORS}


def _trading_days(maps):
    """Sorted list of dates (datetime.date) with any DGS data."""
    days = set()
    for _, sid, _y in TENORS:
        for date_str in maps[sid]:
            try:
                days.add(date.fromisoformat(date_str))
            except ValueError:
                continue
    return sorted(days)


def _last_weekday(d):
    while d.weekday() >= 5:  # Sat=5, Sun=6
        d -= timedelta(days=1)
    return d


def _month_offset(d, months):
    """d minus N calendar months, day clamped to target month length."""
    total = d.year * 12 + (d.month - 1) - months
    y, m = divmod(total, 12)
    m += 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return datetime(y, m, day).date()


def _curve_on(day, maps):
    """{date, points:[{tenor, years, yield}]} for a date, or None."""
    date_str = day.isoformat()
    points = []
    for label, sid, years in TENORS:
        v = maps[sid].get(date_str)
        if v is not None:
            points.append({"tenor": label, "years": round(years, 4), "yield": round(v, 3)})
    if not points:
        return None
    return {"date": date_str, "points": points}


def _fall_backward(exact_date, days):
    """Last available curve date <= exact_date (fall backward per Hong)."""
    for d in reversed(days):
        if d <= exact_date:
            return d
    return None


def get_yield_curve():
    """Yield-curve payload: tenors + per-anchor curves (only those that exist).

    Keys: today, yesterday, 1W, 1M, 3M, 6M, YTD, 1Y, 2Y. today/yesterday may
    be absent on holidays (omitted, never substituted); period-ago keys fall
    backward to the last available curve.
    """
    maps = _tenor_maps()
    days = _trading_days(maps)
    if not days:
        return {"tenors": [t[0] for t in TENORS], "curves": {}}
    try:
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:
        today = datetime.now().date()

    today_eff = _last_weekday(today)
    # today = last available curve date <= today_eff (fall-back, not omit —
    # FRED lags ~6pm ET on a normal trading day; the curve is labeled with
    # its real date so the fallback is honest). yesterday = last WEEKDAY
    # strictly before today's resolved date (never the same day), strict
    # omit if that weekday has no data (holiday rule unchanged).
    today_resolved = _fall_backward(today_eff, days)
    yest_eff = _last_weekday(today_resolved - timedelta(days=1)) if today_resolved else None

    curves = {}
    today_curve = _curve_on(today_resolved, maps) if today_resolved else None
    if today_curve:
        curves["today"] = today_curve
    yest_curve = _curve_on(yest_eff, maps) if yest_eff else None
    if yest_curve:
        curves["yesterday"] = yest_curve

    # 1W: exact offset from the effective today, then fall backward
    w1 = _fall_backward(today_eff - timedelta(days=7), days)
    if w1:
        curves["1W"] = _curve_on(w1, maps)

    # period-ago anchors: calendar offset -> fall backward
    for key, months in (("1M", 1), ("3M", 3), ("6M", 6), ("1Y", 12), ("2Y", 24)):
        exact = _month_offset(today_eff, months)
        d = _fall_backward(exact, days)
        if d:
            curves[key] = _curve_on(d, maps)

    # YTD: first available curve date in the current year
    ytd = next((d for d in days if d >= datetime(today_eff.year, 1, 1).date()), None)
    if ytd:
        curves["YTD"] = _curve_on(ytd, maps)

    return {"tenors": [t[0] for t in TENORS], "curves": curves}


# Module route registration (R2)
ROUTES = {
    '/api/macro': 'handle_macro',
}
