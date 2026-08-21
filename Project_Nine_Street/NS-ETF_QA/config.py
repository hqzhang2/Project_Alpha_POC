"""NS-ETF configuration — combined ETF strategy service (v1).

Consolidates the surviving signal families of NS-1 (ETF rotation),
NS-3-style tiered momentum, and NS-4-PROD trend/composite ratio scoring
into one service shaped like NS-7/NS-8.

Spec: Project_Nine_Street/research_nsetf.md (gitignored, PM-signed off).
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# ── Ports ────────────────────────────────────────────────────────────────
# House convention: QA = PROD + 1. Verified free 2026-08-21.
PORT_QA = 9293
PORT_PROD = 9292

# ── Universe (spec §2a) ──────────────────────────────────────────────────
SECTOR_ETFS = ["XLK", "XLV", "XLF", "XLY", "XLP", "XLE",
               "XLI", "XLB", "XLU", "XLRE"]
BROAD_ETFS = ["SPY"]                      # sole broad-equity anchor
INTL_ETFS = ["EFA", "EEM"]                # INTERNAL rotation only — never fed to NS-5
DEFENSIVE_ETFS = ["TLT", "IEF", "IEI", "AGG", "SHY", "BIL", "SHV"]
REAL_ASSET_ETFS = ["DBC", "GLD"]          # VNQ deferred to v2 (spec §4)

# Full internal rotation universe (allocation-bearing inside NS-ETF)
UNIVERSE = sorted(set(
    SECTOR_ETFS + BROAD_ETFS + INTL_ETFS + DEFENSIVE_ETFS + REAL_ASSET_ETFS))

# Tickers eligible for the NS-5 signals.json sleeve feeds (spec §3/decision #3:
# no international equity via the sleeve channel)
FEED_FED_TICKERS = sorted(set(
    SECTOR_ETFS + BROAD_ETFS + DEFENSIVE_ETFS + REAL_ASSET_ETFS))

CRISIS_SAFE = {"SHY", "BIL", "AGG", "TLT", "IEI", "GLD"}   # from NS-1
CASH_EQ = "BIL"

# ── Signal windows ───────────────────────────────────────────────────────
MOMENTUM_WINDOWS = [21, 63, 126]          # ~1m / 3m / 6m blends (NS-1)
WEEKLY_RANK_WINDOW = 252                  # NS-3 T1: 52w sector-vs-SPY momentum
RSI_PERIOD = 14
BB_PERIOD, BB_STD = 20, 2.0
ADX_PERIOD = 14                            # standard Wilder (fixed vs NS-4)

TOP_N_PER_SLEEVE = 3                       # defensive / real-asset each pick top-N
MAX_POSITION_WEIGHT = 0.40                 # per name within a small sleeve

# ── VIX overlay (from NS-1) ──────────────────────────────────────────────
VIX_CRISIS_LEVEL = 28.0
VIX_SPOT_SERIES = "^VIX"
VIX_AVG_WINDOW = 60                        # trading days for the dashboard avg
VIX_MA_WINDOW = 20                         # VIX moving-average line (NS-1 uses 20MA)
HYG, TLT_MACRO = "HYG", "TLT"              # credit macro ratio input

# ── Regime (NS-3 T3 pattern) ─────────────────────────────────────────────
HMM_SEED = 42                              # deterministic; scales confidence only
REGIME_STORE_PATH = Path(                  # common.regime_store sqlite (read-only)
    "/Users/chuck/Project_Alpha_POC/common/data/regime_store.sqlite")

# ── Sector-ratio advisory panel (from NS-4 PROD; display ONLY) ───────────
ADVISORY_RATIOS = [
    ("XLK/SPY", "Tech vs SPY"), ("XLF/SPY", "Financials vs SPY"),
    ("XLE/SPY", "Energy vs SPY"), ("XLV/SPY", "Healthcare vs SPY"),
    ("XLY/SPY", "Cons.Disc vs SPY"), ("XLP/SPY", "Cons.Staples vs SPY"),
    ("XLB/SPY", "Materials vs SPY"), ("XLI/SPY", "Industrials vs SPY"),
    ("XLU/SPY", "Utilities vs SPY"), ("XLRE/SPY", "RealEstate vs SPY"),
]

# ── Feed artifact ────────────────────────────────────────────────────────
SIGNALS_PATH = DATA_DIR / "signals.json"
FEED_VERSION = 1

# ── Refresh cache TTLs (seconds) ─────────────────────────────────────────
PRICE_CACHE_TTL = 300
