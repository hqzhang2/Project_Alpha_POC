"""
Alpha Terminal Configuration
"""
import os

# Server config
DEFAULT_PORT = 9098
HOST = '0.0.0.0'

# Snapshot scanner config
SCAN_EXCHANGES = ['NYSE', 'NASDAQ']
HIGH_WINDOW = 252
LOW_WINDOW = 252
AT_HIGH_THRESHOLD_PCT = 2.0
AT_LOW_THRESHOLD_PCT = 2.0

# Yahoo Finance config
YF_CACHE_MINUTES = 5

# Chart timeframes
TIMEFRAME_MAP = {
    '1D': '1d',
    '1W': '1wk', 
    '1M': '1mo',
    '3M': '3mo',
    '6M': '6mo',
    'YTD': 'ytd',
    '1Y': '1y',
    '2Y': '2y',
    '5Y': '5y'
}

# Default watchlists
DEFAULT_WATCHLIST = ['SPY', 'QQQ', 'IWM', 'TLT']
RATIO_WATCHLIST = ['XLE/SPY', 'TLT/SPY', 'GLD/SPY']

# Options config
OTM_STRIKES = 10  # Number of OTM strikes to show
STANDARD_EXPIRY_DAY_RANGE = (15, 21)  # Days for standard 3rd Friday

# Indicator defaults
DEFAULT_SMA_PERIOD = 200
RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# UI config
THEME = 'dark'
CHART_COLORS = {
    'primary': '#3fb950',
    'secondary': '#f0883e', 
    'calls': '#3fb950',
    'puts': '#f85149',
    'volume': 'rgba(88, 166, 255, 0.3)'
}

# --- Option Screener v2.4 ---
OPTION_DATA_PROVIDER = "yfinance"  # default feed; UI toggle overrides per request
# Universe = SCREENER_WATCHLIST + SCREENER_LIQUID_POOL + earnings names (gated).
SCREENER_WATCHLIST = [  # seed set (was terminal/watchlist.json; QA has no file, so config is source of truth)
    "AAPL", "TSLA", "MSFT", "GOOGL", "AMZN", "NVDA", "SPY", "QQQ", "GLD", "SMH", "XLE",
]
SCREENER_LIQUID_POOL = [
    # --- core 30 (2026-08-03): mega-cap tech, banks, energy, biotech, high-beta ---
    "META", "NFLX", "AVGO", "ORCL", "AMD", "INTC", "CRM", "ADBE", "QCOM", "MU",
    "T", "VZ", "BAC", "JPM", "XOM", "CVX", "LLY", "UNH", "JNJ", "PFE",
    "COIN", "MSTR", "TSM", "BABA", "PLTR", "SOFI", "UBER", "ABNB", "NIO", "MRVL",
    # --- +20 (2026-08-03, load-checked 9.6s / 60 names / +29MB / 437KB = pass) ---
    "DELL", "SMCI", "ARM", "CRWD", "SNOW", "PANW", "HOOD", "SHOP", "PYPL",
    "V", "MA", "GS", "MS", "C", "WFC", "WMT", "KO", "ABBV", "MRK", "BA",
]
SCREENER_MAX_EXPIRIES = 4          # next 4 expiries per ticker (~0-90 DTE)
SCREENER_MAX_WORKERS = 8           # thread pool for chain fetches
SCREENER_CACHE_TTL = 600           # seconds; universe scan cache
SCREENER_MIN_DTE = 2               # contracts with dte <= this are damped x0.3
SCREENER_INDEX_TICKERS = {"SPY", "QQQ", "IWM", "DIA"}
SCREENER_EARNINGS_WINDOW_DAYS = 14
SCREENER_EARNINGS_CACHE_TTL = 86400  # 24h earnings cache
SCREENER_MAX_UNIVERSE = 60         # 40->60 (Hong, 2026-08-03: load check 9.6s/+29MB/437KB = pass)
SCREENER_EARNINGS_MIN_MCAP = 1e11  # auto-added earnings names must be > $100B market cap
SCORE_WEIGHTS = {                  # composite score weights (sum = 1.0)
    "vol_oi_z": 0.30, "notional_z": 0.25, "moneyness": 0.20,
    "iv_cheap": 0.15, "catalyst": 0.10,
}
SCORE_WEIGHTS_OI = {               # used when OI-build history exists (Phase 2 store):
    "vol_oi_z": 0.20, "oi_build_z": 0.10, "notional_z": 0.25,  # 0.10 shifted from vol_oi_z
    "moneyness": 0.20, "iv_cheap": 0.15, "catalyst": 0.10,
}
SCORE_TIER_HIGH = 2.5              # score >= this -> HIGH
SCORE_TIER_MED = 1.5               # score >= this -> MED (below -> LOW)

# --- Option Screener v2.4 — OI snapshot store (Phase 2) ---
OI_BUILD_WINDOWS = (1, 5, 20)      # build-% horizons (trading-ish days, calendar approx)
OI_DIVERGENCE_BUILD = 0.15         # oi_build_5d >= +15% ...
OI_DIVERGENCE_SPOT = 0.01          # ... while |spot change| <= 1% -> accumulation tell
OI_MIN_HISTORY_DAYS = 3            # signals only after this many stored days

# --- Option Screener v2.4 — Polygon.io provider (free tier, delayed) ---
POLYGON_API_KEY_ENV = "POLYGON_API_KEY"   # env-only; no key in source (news.py rule)
POLYGON_BASE = "https://api.polygon.io"
POLYGON_RATE_PER_MIN = 5          # free tier = 5 req/min; Starter+ = higher
POLYGON_CHAIN_TTL = 60            # seconds; per-underlying snapshot cache (scan reuses it)
POLYGON_CACHE_TTL = 1800          # seconds; universe scan cache on polygon (slow scans -> long TTL)
