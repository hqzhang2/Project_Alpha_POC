#!/usr/bin/env python3
"""
NS-5 Factor Model — configuration (single source of truth).

All factor tickers, window sizes, and thresholds live here.
Per v1 roadmap §1.2: no hardcoded factor tickers in logic code.
"""
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
TESTS_DIR = BASE_DIR / "tests"

# ---------------------------------------------------------------------------
# Factor model (v1 roadmap §8.2 — frontier-approved, do not change)
# ---------------------------------------------------------------------------
# Factor construction:
#   MKT = SPY return - risk-free (^IRX annualized / 252)
#   SMB = IWM return - SPY return            (small minus large)
#   HML = VTV return - VUG return            (value minus growth)
#   MOM = MTUM return - SPY return           (momentum spread)
#   DUR = TLT return                          (long-duration exposure)
FACTOR_TICKERS = {
    "SPY": "MKT_LONG",       # market
    "IWM": "SMB_LONG",       # small-cap
    "VTV": "HML_LONG",       # value
    "VUG": "HML_SHORT",      # growth
    "MTUM": "MOM_LONG",      # momentum
    "TLT": "DUR_LONG",       # long bond
}
RISK_FREE_TICKER = "^IRX"    # 13-week T-bill, annualized yield %

FACTOR_DEFINITIONS = {
    # name: (long_ticker, short_ticker_or_None, kind)
    # kind: 'excess' = raw - rf_daily ; 'spread' = long - short ; 'raw' = long return
    "MKT": ("SPY", None, "excess"),
    "SMB": ("IWM", "SPY", "spread"),
    "HML": ("VTV", "VUG", "spread"),
    "MOM": ("MTUM", "SPY", "spread"),
    "DUR": ("TLT", None, "raw"),
}
FACTOR_NAMES = list(FACTOR_DEFINITIONS.keys())  # ["MKT", "SMB", "HML", "MOM", "DUR"]

# ---------------------------------------------------------------------------
# Data windows (v1 roadmap — frontier-approved, do not change)
# ---------------------------------------------------------------------------
DATA_YEARS = 2                 # fetch 2 years of daily closes
REGRESSION_WINDOW = 250        # rolling OLS window (trading days)
REGRESSION_STEP = 21           # monthly step (~21 trading days)
VOL_WINDOW_SHORT = 60          # short vol window (trading days)
VOL_WINDOW_LONG = 250          # long vol window
CORR_WINDOW = 120              # pairwise correlation window
VOL_RATIO_THRESHOLD = 1.5      # 60d/250d vol ratio flag threshold
CORR_SHIFT_THRESHOLD = 0.3     # |corr_60d - corr_250d| flag threshold
MIN_PERIODS_PCT = 0.8          # rolling windows require >= 80% of window populated

# Regression design matrix: intercept + 5 factors
REGRESSORS = ["intercept"] + FACTOR_NAMES

# ---------------------------------------------------------------------------
# Yahoo fetch parameters
# ---------------------------------------------------------------------------
YF_PERIOD = "2y"
YF_INTERVAL = "1d"
YF_AUTO_ADJUST = True
YF_PROGRESS = False

# Cache TTL: refresh if the latest cached bar is older than this (weekend-safe)
CACHE_MAX_AGE_DAYS = 3

# ── Sleeve blend (2b — joint universe, DESIGN §4.3) ─────────────────────
# PROD-side: growth sleeve = NS-7_PROD feed (its own daily refresh store);
# value sleeve = A_T PROD screener (9098); regime = common.regime_store.
NS7_SELECTION_PATH = Path(
    "/Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-7_PROD/data/selection.json")
AT_SCREENER_URL = "http://127.0.0.1:9098/api/fundamentals/screen"
VALUE_SLEEVE_N = 20
# PM-decidable tilt (2a evidence: momentum carries the engine — asymmetric).
SLEEVE_TILT = {"growth": (0.80, 0.20), "defensive": (0.50, 0.50)}
BLEND_PATH = DATA_DIR / "sleeve_blend.json"

# ── v4.5 feed sources (NS-5 grades D1 / NS8 / NSETF / ALL) ──────────────
# Each source is a weighted book NS-5 can grade on its own (single) or merged
# (ALL). Fail-open: missing/stale source contributes nothing.
# PROD paths: D1 = NS-7_PROD basket; NS-8 = PROD signals; NS-ETF = QA signals
# (NS-ETF PROD twin not yet live — see AGENTS.md port table).
D1_BASKET_PATH = Path(
    "/Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-7_PROD/data/d1_basket.json")
NS8_SIGNALS_PATH = Path(
    "/Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-8_PROD/data/signals.json")
NSETF_SIGNALS_PATH = Path(
    "/Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-ETF_QA/data/signals.json")
# Feed staleness (days): a source older than this is treated as absent.
FEED_STALE_DAYS = 5
# Valid source keys for the grade dropdown (single sources + the ALL merge).
FEED_SOURCES = ("D1", "NS8", "NSETF", "ALL")
