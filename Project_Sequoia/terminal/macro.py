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
  - Parallel cold-fill + background pre-warm (2026-08-10): a request fills
    every stale FRED series and the two Yahoo payloads concurrently
    (ThreadPoolExecutor), and a daemon thread refreshes the cache every 30
    minutes so the tab never pays the cold rebuild on page load (PROD
    measured 2026-08-10: 8.95s cold vs 17ms warm; the old code fetched
    ~30 FRED series + 2 Yahoo downloads sequentially inside the request).
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
from concurrent.futures import ThreadPoolExecutor
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
        {"id": "DGS30", "name": "30Y Treasury", "unit": "%", "cadence": "Daily"},
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
        if not _is_stale(cache_key):
            return _cache[cache_key][1]
    start = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    obs = _observations(series_id, start, item.get("units"))
    with _lock:
        _cache[cache_key] = (time.time(), obs)
    return obs


# ---------------------------------------------------------------------------
# Parallel cold-fill + background pre-warm (Hong, 2026-08-10).
# Cold loads used to fetch ~30 FRED series + 2 Yahoo downloads sequentially
# inside the request -> ~9s first hit after any TTL expiry. Now a request
# fills every stale entry concurrently, and a daemon thread refreshes the
# cache every 30 min (staying ahead of the 1h Daily TTL) so page loads are
# almost always served from a warm cache. Tests disable the daemon via
# _prewarm_enabled = False (autouse fixture in test_macro.py).
# ---------------------------------------------------------------------------
PREWARM_INTERVAL = 1800  # seconds; < Daily TTL (3600) so the cache never goes stale
_prewarm_enabled = True
_prewarm_started = False
_prewarm_lock = threading.Lock()
_prewarm_stop = threading.Event()


def _all_series_keys():
    """Every FRED cache key the page needs: catalog raw + computed sources + tenors.

    The stock-bond corr item's `from` (SPY/TLT) are Yahoo tickers, NOT FRED
    series — they are fetched separately by _corr_60d() (CORR_CACHE_KEY).
    """
    keys = set()
    for g in GROUPS:
        for it in g["items"]:
            if it.get("computed") == "corr":
                continue
            for sid in it.get("from", [it["id"]]):
                keys.add(sid + ":" + it.get("units", ""))
    for _label, sid, _years in TENORS:
        keys.add(sid + ":")
    return sorted(keys)


def _is_stale(key):
    """True when a cache entry is missing or older than its cadence TTL.

    Single source of truth for the TTL check — get_series and
    _prefetch_missing both use it.
    """
    sid = key.split(":")[0]
    item = SERIES_BY_ID.get(sid, {})
    ttl = TTL.get(item.get("cadence", ""), DEFAULT_TTL)
    cached = _cache.get(key)
    return not cached or time.time() - cached[0] >= ttl


def _prefetch_missing(max_workers=8):
    """Fill every stale cache entry in parallel (FRED series + the two Yahoo
    payloads). Only missing/expired work is done — warm entries are skipped,
    so this is cheap on every request and from the pre-warm thread. Fail-open:
    each fetcher degrades to [] / None on error, as before.
    """
    with _lock:
        stale = [k for k in _all_series_keys() if _is_stale(k)]
    if not stale:
        return
    sids = sorted({k.split(":")[0] for k in stale})
    fns = [lambda sid=sid: get_series(sid) for sid in sids]
    fns += [_corr_60d, _yahoo_yield_curve]
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            list(ex.map(lambda f: f(), fns))
    except Exception as e:
        logger.warning("macro prefetch failed (fail-open): %s", e)


def start_prewarm(interval=PREWARM_INTERVAL):
    """Start the background refresher once (idempotent). No-op when disabled
    (tests). Daemon thread — dies with the process, restarts on next deploy.
    """
    global _prewarm_started
    if not _prewarm_enabled:
        return
    with _prewarm_lock:
        if _prewarm_started:
            return
        _prewarm_started = True
    t = threading.Thread(target=_prewarm_loop, args=(interval,),
                         daemon=True, name="macro-prewarm")
    t.start()
    logger.info("macro pre-warm thread started (interval %ss)", interval)


def _prewarm_loop(interval):
    while _prewarm_enabled and not _prewarm_stop.wait(interval):
        try:
            _prefetch_missing()
        except Exception as e:
            logger.warning("macro pre-warm tick failed: %s", e)


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

YAHOO_YC_KEY = "YAHOO-YC:curve"  # live yield-curve fallback, Daily TTL
# Yahoo treasury symbols: only 4 of our 11 tenors exist as quotes.
#   ^IRX = 13-week T-bill (3M), ^FVX = 5Y, ^TNX = 10Y, ^TYX = 30Y
YAHOO_YC_TENORS = [("^IRX", "3M", 0.25), ("^FVX", "5Y", 5.0),
                   ("^TNX", "10Y", 10.0), ("^TYX", "30Y", 30.0)]


def _yahoo_yield_curve():
    """Recent daily yield curves from Yahoo closes: {source, curves:[...]} or None.

    `curves` is oldest->newest, at least the last two trading days, each
    {date, source: 'yahoo', points:[{tenor, years, yield}]} (4 tenors:
    ^IRX/^FVX/^TNX/^TYX = 3M/5Y/10Y/30Y). Fallback for the today/yesterday
    anchors only — FRED wins once it publishes (Hong, 2026-08-10: "Yahoo data
    is always fallback data for the day's yield curve, except weekends").
    Cached Daily like _corr_60d; fail-open (None) on any error so the FRED
    fall-back path stays intact.
    """
    with _lock:
        cached = _cache.get(YAHOO_YC_KEY)
        if cached and time.time() - cached[0] < TTL["Daily"]:
            return cached[1]
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance unavailable; live yield curve skipped")
        return None
    try:
        syms = [s for s, _t, _y in YAHOO_YC_TENORS]
        px = yf.download(syms, period="5d", interval="1d",
                         auto_adjust=False, progress=False)
        if px is None or px.empty:
            return None
        close = px["Close"].dropna(how="all")
        if close.empty:
            return None
        out = {"source": "yahoo", "curves": []}
        for idx, row in close.tail(2).iterrows():
            points = []
            for sym, tenor, years in YAHOO_YC_TENORS:
                v = row.get(sym)
                if v is not None and v == v:  # not NaN
                    points.append({"tenor": tenor, "years": years,
                                   "yield": round(float(v), 3)})
            if points:
                out["curves"].append({"date": str(idx)[:10],
                                      "source": "yahoo", "points": points})
        if not out["curves"]:
            return None
        with _lock:
            _cache[YAHOO_YC_KEY] = (time.time(), out)
        return out
    except Exception as e:
        logger.warning("live yield curve failed (fail-open): %s", e)
        return None


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
    start_prewarm()       # lazily start the background refresher (idempotent)
    _prefetch_missing()   # parallel cold-fill; no-op when the cache is warm
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

    Keys: today, yesterday, 1W, 1M, 3M, 6M, YTD, 1Y, 2Y. Today is the EFFECTIVE
    today — Yahoo's live close when fresher than FRED, else FRED's resolved
    date (Hong 2026-08-10). Yesterday is the CALENDAR day before the effective
    today, labeled with its real date, fed from FRED when it has that date,
    else Yahoo (same fallback as today), else the last available curve (Hong
    2026-08-11). Period-ago anchors offset from the effective today and fall
    backward to the last available curve (never omitted when history exists).
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
    # today = last available curve date <= today_eff (fall-back, not omit).
    today_resolved = _fall_backward(today_eff, days)
    # Live today override (Hong, 2026-08-10): Yahoo closes (^IRX/^FVX/^TNX/
    # ^TYX — 3M/5Y/10Y/30Y only) replace the today anchor WHEN fresher than
    # FRED's resolved date. Weekdays before ~6pm: Yahoo has today, FRED
    # doesn't -> live curve. Weekends: Yahoo's last close is Friday == FRED's
    # resolved Friday -> no override. Once FRED publishes today (~6pm),
    # FRED's resolved date == Yahoo's date -> no override (FRED wins).
    # Yahoo fallback resolves the effective today (and can feed yesterday):
    # _yahoo_yield_curve() returns the last two trading days' curves.
    yahoo = _yahoo_yield_curve()
    yahoo_by_date = {}
    if yahoo:
        for c in yahoo.get("curves", []):
            yahoo_by_date[c["date"]] = c
    yahoo_dates = sorted(yahoo_by_date)

    # Effective today = freshest Yahoo date when it beats FRED's resolved date,
    # else FRED (Hong, 2026-08-10 override rule).
    if yahoo_dates and today_resolved and yahoo_dates[-1] > today_resolved.isoformat():
        anchor = date.fromisoformat(yahoo_dates[-1])
        today_curve = yahoo_by_date[yahoo_dates[-1]]
    else:
        anchor = today_resolved
        today_curve = _curve_on(anchor, maps) if anchor else None

    curves = {}
    if anchor:
        if today_curve:
            curves["today"] = today_curve
        # yesterday = the CALENDAR day before the effective today, labeled with
        # its real date. Fed from FRED when it has that date, else Yahoo (the
        # same fallback today uses), else the last available curve (Hong,
        # 2026-08-11: "Yesterday is Aug 10, not Aug 7").
        yest_date = anchor - timedelta(days=1)
        yest_curve = _curve_on(yest_date, maps)
        if not yest_curve:
            yest_curve = yahoo_by_date.get(yest_date.isoformat())
        if not yest_curve:
            yest_resolved = _fall_backward(yest_date, days)
            if yest_resolved:
                yest_curve = _curve_on(yest_resolved, maps)
        if yest_curve:
            curves["yesterday"] = yest_curve
        # 1W + period-ago: calendar offset from the effective today, fall back.
        w1 = _fall_backward(anchor - timedelta(days=7), days)
        if w1:
            curves["1W"] = _curve_on(w1, maps)
        for key, months in (("1M", 1), ("3M", 3), ("6M", 6), ("1Y", 12), ("2Y", 24)):
            d = _fall_backward(_month_offset(anchor, months), days)
            if d:
                curves[key] = _curve_on(d, maps)

    # YTD: first available curve date in the current year
    ytd_year = (anchor or today_eff).year
    ytd = next((d for d in days if d >= datetime(ytd_year, 1, 1).date()), None)
    if ytd:
        curves["YTD"] = _curve_on(ytd, maps)

    return {"tenors": [t[0] for t in TENORS], "curves": curves}


# Module route registration (R2)
ROUTES = {
    '/api/macro': 'handle_macro',
}
