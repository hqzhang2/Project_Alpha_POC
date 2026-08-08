"""Macro economics data module for Alpha Terminal (display-only).

Macro page = 6 categories (Growth & Labor, Inflation, Monetary & Yield
Curve, Credit & Financial Conditions, External, Markets), each a set of
FRED time series. Plain data graphs — no sentiment, no NS-5 portfolio
grading (Hong scope, 2026-08-08).

Design:
  - FRED v1 API, key from env only (FRED_API_KEY in QA/PROD plists).
  - Fail-open: missing key / API error -> [] per series, never a crash.
  - TTL cache per series (daily 1h, weekly 6h, monthly 24h, quarterly 48h)
    so the page is cheap on reload.
  - Computed series: 2s10s spread, BAA-AAA spread, stock-bond 60d corr.
  - R2: ROUTES = {'/api/macro': 'handle_macro'}; handler method on Handler
    class in server.py; module registered in _discover_module_routes().
"""

import json
import logging
import os
import threading
import time
import urllib.request
from datetime import datetime, timedelta

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


def _corr_60d():
    """Stock–bond 60d rolling correlation from SPY + TLT daily returns."""
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
            "configured": configured, "groups": groups}


# Module route registration (R2)
ROUTES = {
    '/api/macro': 'handle_macro',
}
