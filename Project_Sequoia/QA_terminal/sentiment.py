"""Sentiment strip module (v2 schema) for Alpha Terminal QA.

Design (approved 2026-08-03, feature/v2.9):
  - readings fact table + metric_definitions dimension (see sentiment_db.py).
  - One sentiment axis everywhere: +1 = max bullish, -1 = max bearish/fear.
  - Raw `value` is stored uninterpreted; `sentiment` is derived via the
    normalization method declared in metric_definitions — interpretation lives
    in ONE place (this module + the definitions table), never in collectors/UI.
  - Fail-open: missing key / API error / DB error -> [] or None, never a crash.
  - FREE-only sources, forward-only collection (no backfill).

Collectors register via PROVIDERS and write readings through sentiment_db.
Normalization helpers are used BY collectors (percentiles need trailing history).
"""
import logging

import sentiment_db as db

logger = logging.getLogger(__name__)

# Env var for the free Alpha Vantage API key (never in code; QA plist
# EnvironmentVariables). NEWS_SENTIMENT is a premium endpoint — the demo key
# is excluded from it; a real free key is required.
AV_API_KEY_ENV = "ALPHA_VANTAGE_API_KEY"

# Env var for the free Firecrawl API key (never in code; QA plist
# EnvironmentVariables). Needed to bypass AAII's Imperva bot protection.
AAII_API_KEY_ENV = "FIRECRAWL_API_KEY"

# ---------------------------------------------------------------------------
# metric_definitions seed — every metric the strip can carry (13), incl. deferred
# (1f COT, 1g margin debt, 1h insider) so the schema is stable from day one.
# Market-sentiment only: consumer-confidence surveys (UMich/Conf Board) are
# macro data — removed per Hong 2026-08-07.
# ---------------------------------------------------------------------------

METRIC_DEFINITIONS = [
    # --- Tier 1: market-implied -------------------------------------------
    {"metric": "put_call_oi_ratio", "display_name": "Put/Call OI", "scope": "ticker",
     "unit": "ratio", "higher_is": "bearish", "normalization": "call_share", "window": 252,
     "source_default": "oi_store"},
    {"metric": "breadth_ad", "display_name": "A/D Breadth", "scope": "market",
     "unit": "share", "higher_is": "bullish", "normalization": "passthrough", "window": None,
     "source_default": "breadth"},
    {"metric": "vix", "display_name": "VIX", "scope": "market",
     "unit": "pts", "higher_is": "bearish", "normalization": "percentile_inv", "window": 252,
     "source_default": "cboe"},
    {"metric": "cboe_pc_equity", "display_name": "Equity P/C", "scope": "market",
     "unit": "ratio", "higher_is": "bearish", "normalization": "percentile_inv", "window": 252,
     "source_default": "cboe"},
    {"metric": "cboe_pc_index", "display_name": "Index P/C", "scope": "market",
     "unit": "ratio", "higher_is": "bearish", "normalization": "percentile_inv", "window": 252,
     "source_default": "cboe"},
    {"metric": "short_interest_dtc", "display_name": "Short Int. DTC", "scope": "ticker",
     "unit": "days", "higher_is": "bearish", "normalization": "percentile_inv", "window": 52,
     "source_default": "finra"},
    {"metric": "cot_net_spec", "display_name": "COT Net Spec", "scope": "market",
     "unit": "contracts", "higher_is": "bullish", "normalization": "percentile", "window": 252,
     "source_default": "cot"},                      # deferred (next release)
    {"metric": "margin_debt", "display_name": "Margin Debt", "scope": "market",
     "unit": "$B", "higher_is": "bullish", "normalization": "percentile", "window": 252,
     "source_default": "finra_margin"},                  # deferred (next release)
    {"metric": "insider_net_buy", "display_name": "Insider Net Buy", "scope": "ticker",
     "unit": "$M", "higher_is": "bullish", "normalization": "percentile", "window": 252,
     "source_default": "edgar"},                    # deferred (next release)
    # --- Tier 2: surveys ---------------------------------------------------
    {"metric": "aaii_bull_bear_spread", "display_name": "AAII Bull-Bear", "scope": "market",
     "unit": "pct", "higher_is": "bullish", "normalization": "spread_100", "window": None,
     "source_default": "aaii"},
    {"metric": "naaim_exposure", "display_name": "NAAIM Exposure", "scope": "market",
     "unit": "pts", "higher_is": "bullish", "normalization": "center_50", "window": None,
     "source_default": "naaim"},
    # --- Tier 3: news/social (Phase 5) -------------------------------------
    {"metric": "news_sentiment", "display_name": "News Sentiment", "scope": "market",
     "unit": "score", "higher_is": "bullish", "normalization": "passthrough", "window": None,
     "source_default": "alphavantage"},
    {"metric": "social_volume", "display_name": "Social Volume", "scope": "ticker",
     "unit": "msgs", "higher_is": "bullish", "normalization": None, "window": None,
     "source_default": "stocktwits"},
    {"metric": "social_bull_bear", "display_name": "Social Bull-Bear", "scope": "ticker",
     "unit": "spread", "higher_is": "bullish", "normalization": "passthrough", "window": None,
     "source_default": "stocktwits"},
]


def seed():
    """Seed metric_definitions (idempotent). Call once at server start."""
    return db.seed_metrics(METRIC_DEFINITIONS)


# ---------------------------------------------------------------------------
# Normalization helpers — used by collectors to derive sentiment from value.
# Percentile methods are TRAILING (only history available at that date), which
# keeps walk-forward honesty: no full-sample lookahead, ever.
# ---------------------------------------------------------------------------

def _clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def _pct_rank(value, history):
    """Percentile rank of `value` within trailing `history` (list of floats).
    Returns None when there is no usable history. Rank = fraction strictly below."""
    vals = [v for v in (history or []) if v is not None]
    if not vals or value is None:
        return None
    below = sum(1 for v in vals if v < value)
    return below / len(vals)


# Percentile-based sentiment needs a meaningful sample; with a handful of
# points the rank is degenerate (±1 on any move) and misleading. Below this
# threshold the strip shows gray (no score) — walk-forward honest.
MIN_PERCENTILE_HISTORY = 20


def normalize(value, method, history=None):
    """Derive sentiment (-1..+1) from raw value per declared method. None on any miss."""
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if method is None or method == "":
        return None
    if method == "passthrough":
        return _clamp(value)
    if method == "call_share":
        # ratio = put/call -> call share = 1/(1+ratio) -> sentiment = 2*share - 1
        if value <= 0:
            return None
        return _clamp(2.0 / (1.0 + value) - 1.0)
    if method == "spread_100":
        return _clamp(value / 100.0)
    if method == "center_50":
        return _clamp((value - 50.0) / 50.0)
    if method in ("percentile", "percentile_inv"):
        if len([v for v in (history or []) if v is not None]) < MIN_PERCENTILE_HISTORY:
            return None  # too little history for a meaningful rank
        rank = _pct_rank(value, history)
        if rank is None:
            return None
        sent = 2.0 * rank - 1.0
        return -sent if method == "percentile_inv" else sent
    logger.warning("Unknown normalization method: %s", method)
    return None


# ---------------------------------------------------------------------------
# Query layer (server handlers call these)
# ---------------------------------------------------------------------------

def get_sentiment(scope=None, ticker=None, metric=None, days=None, sources=None, latest=False):
    """Readings joined with definitions, newest first. Fail-open -> [].

    latest=True returns one row per (scope, ticker, metric, source) = its most
    recent reading — the strip's default view (snapshot, not log).
    """
    return db.query_readings(scope=scope, ticker=ticker, metric=metric, days=days,
                             sources=sources, latest=latest)


def get_metrics():
    return db.get_metrics()


def list_providers():
    """Configured status per provider name.

    Keyless providers are configured when registered; key-gated providers
    (alphavantage, aaii) report configured only when their env key is present.
    """
    import os as _os
    keyed = {
        "alphavantage": AV_API_KEY_ENV,
        "aaii": AAII_API_KEY_ENV,
    }
    out = []
    for name in sorted(PROVIDERS):
        env = keyed.get(name)
        configured = bool(_os.environ.get(env)) if env else True
        out.append({"name": name, "configured": configured})
    return out


def latest_date():
    return db.latest_reading_date()


# Provider registry — collectors register here as phases land.
PROVIDERS = {}


def register_provider(name, collector):
    """Register a collector callable: collector() writes readings, returns count."""
    PROVIDERS[name] = collector


def run_collectors(sources=None):
    """Run registered collectors (deterministic, fail-open each). Returns {source: n}."""
    results = {}
    for name, collector in PROVIDERS.items():
        if sources and name not in sources:
            continue
        try:
            results[name] = collector()
        except Exception as e:
            logger.error("Collector %s failed (fail-open): %s", name, e)
            results[name] = 0
    return results


# Module route registration (R2) — server.py implements the handler methods.
ROUTES = {
    "/api/sentiment": "handle_sentiment",
    "/api/sentiment/metrics": "handle_sentiment_metrics",
    "/api/sentiment/providers": "handle_sentiment_providers",
    "/api/sentiment/ticker": "handle_sentiment_ticker",
}


def get_ticker_sentiment(ticker):
    """Per-ticker dashboard payload: chip headlines + drill-down detail.

    {
      "insider": {"value": -15.9547, "unit": "$M", "sentiment": null,
                  "count": 10, "asof_date": "...", "filings": [...]},
      "social":  {"spread": 0.5, "sentiment": 0.5, "count": 599,
                  "classified": 192, "asof_date": "...", "daily": [...]}
    }
    Fail-open: every field present, empty lists/None when no data.
    """
    import sentiment_db as db
    out = {
        "insider": {"value": None, "unit": "$M", "sentiment": None, "count": 0,
                    "asof_date": None, "filings": []},
        "social": {"spread": None, "sentiment": None, "count": 0, "classified": 0,
                   "asof_date": None, "daily": []},
    }
    try:
        rows = db.query_readings(ticker=ticker, latest=True)
        for r in rows:
            if r.get("metric") == "insider_net_buy":
                out["insider"].update({"value": r.get("value"), "sentiment": r.get("sentiment"),
                                       "count": r.get("count") or 0, "asof_date": r.get("asof_date")})
            elif r.get("metric") == "social_bull_bear":
                out["social"].update({"spread": r.get("value"), "sentiment": r.get("sentiment"),
                                      "classified": r.get("count") or 0, "asof_date": r.get("asof_date")})
            elif r.get("metric") == "social_volume":
                out["social"]["count"] = r.get("count") or 0
        out["insider"]["filings"] = db.query_filings(ticker, limit=50)
        out["social"]["daily"] = db.query_social_daily(ticker, limit=14)
    except Exception:
        pass
    return out


# Register Phase 2/3 collectors (oi_store, breadth, cboe, finra) — must come
# after PROVIDERS exists; sentiment_collect imports sentiment lazily, so no
# circular import.
try:
    from sentiment_collect import register_all as _register_all
    _register_all()
except ImportError:
    logger.warning("sentiment_collect not available — collectors not registered")

# Seed metric definitions at import (idempotent upsert) so the definitions
# table is always populated — joins and /api/sentiment/metrics depend on it.
seed()

