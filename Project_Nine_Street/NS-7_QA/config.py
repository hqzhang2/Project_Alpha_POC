"""config.py — NS-7 Growth/Momentum Selection service configuration.

All thresholds live here so the walk-forward harness and the live server share
one source of truth (the NS-6 config.py pattern). Nothing hardcoded downstream.
"""
import os

# ── Service identity ────────────────────────────────────────────────────
PORT = int(os.environ.get("PORT", 9271))          # QA; PROD 9270 (reserved)
ENV = os.environ.get("ENV", "QA")

# ── Universe eligibility (§3.1 of DESIGN.md) ────────────────────────────
MARKET_CAP_MIN = 50_000_000_000.0                 # U2: > $50B (league floor)
# PM fast-track (2026-08-13): a NON-SP500 name breaches $75B → immediate
# Major, no 90-day wait. SP500 membership alone is already immediate Major.
MARKET_CAP_MAJOR_FASTTRACK = 75_000_000_000.0
MIN_AVG_DAILY_VOLUME = 100_000.0                  # U3: > 100K shares/day (20d avg)
VOLUME_WINDOW_DAYS = 20                           # U3 averaging window

# ── Quality floor (§5 G3 — a VETO, deliberately loose) ─────────────────
REQUIRE_POSITIVE_EPS = True                        # U4a: trailing-12m EPS > 0
REQUIRE_POSITIVE_CFO = True                        # U4b: trailing-12m CFO > 0

# ── Two-league system (§3.2) ────────────────────────────────────────────
GRACE_PERIOD_DAYS = 90                             # promote/demote/expire clock
LEAGUE_MAJOR = "major"
LEAGUE_MINOR = "minor"
LEAGUE_REMOVED = "removed"

# ── Momentum signal (§4.1) ──────────────────────────────────────────────
MOMENTUM_LOOKBACK_DAYS = 126                       # ~6 months
MOMENTUM_SKIP_DAYS = 21                            # ~1 month skip (reversal filter)
MOMENTUM_MIN_HISTORY = MOMENTUM_LOOKBACK_DAYS + 1  # need full series

# ── Ranking & selection (§4.2) ──────────────────────────────────────────
TOP_N = 20                                          # top-N Major names selected
# Anti-churn cushion (G5): a HELD name ranked up to TOP_N + TURNOVER_BAND
# stays in the book instead of being trimmed on a transient rank wobble.
TURNOVER_BAND = 10

# ── Guardrail caps (§5) ─────────────────────────────────────────────────
MAX_POSITION_WEIGHT = 0.08                          # G4: 8% per name
MIN_EFFECTIVE_N = 15                                # G4: min effective-N
MAX_SECTOR_WEIGHT = 0.40                            # G4: 40% sector cap

# ── Data path (module-relative, like NS-6 store.py) ────────────────────
import pathlib
DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "ns7.db"

# ── A_T data sources (READ-ONLY — NS-7 never writes to A_T stores) ─────
# NS-7 consumes A_T's point-in-time store via direct SQLite reads (decoupled
# file-read pattern, same as NS-6 reading NS-5 portfolios.json). Paths are
# env-overridable so tests can point at a fixture DB.
_AT_TERMINAL = pathlib.Path(__file__).resolve().parent.parent.parent / "Project_Sequoia" / "terminal"
AT_FUNDAMENTALS_DB = pathlib.Path(
    os.environ.get("AT_FUNDAMENTALS_DB", str(_AT_TERMINAL / "data" / "fundamentals_hist.db"))
)
AT_SP500_CACHE = pathlib.Path(
    os.environ.get("AT_SP500_CACHE", str(_AT_TERMINAL / "data" / "sp500.json"))
)

# ── Volume pipeline (U3) ────────────────────────────────────────────────
# U3 needs a 20-day average daily volume. A_T's price store carries closes
# only, so NS-7 keeps its own volume table (yfinance, same source as A_T).
VOLUME_FETCH_WINDOW_DAYS = 40        # fetch ~2 months, use last 20 (margin)
VOLUME_STALE_DAYS = 10               # refetch when newest volume older than this
VOLUME_REFETCH_BATCH = 50            # yfinance calls per batch (politeness)

# ── Selection output (the NS-5 feed) ────────────────────────────────────
SELECTION_PATH = DATA_DIR / "selection.json"

# ── Benchmark filter (PM 2026-08-13): show picks beating BOTH SPY and QQQ ─
# over the same 126/21 skip-month window. SPY/QQQ closes fetched via
# yfinance and cached (the A_T store carries no index series).
BENCH_SYMBOLS = ["SPY", "QQQ"]
BENCH_CACHE = DATA_DIR / "bench_closes.json"

# ── Walk-forward harness (G1 acceptance gate) ───────────────────────────
WF_START = "2016-01-01"              # first rebalance month
WF_END = "2026-07-31"                # last rebalance month
# Quarterly rebalance — matches the production operational rhythm
# (full_stack_review_v2 §10: selector re-run quarterly). Tested vs monthly
# (2026-08): both pass G1 at 8/11 excess years; quarterly halves annual
# turnover (3.1 vs 5.9 book-turns/yr) and improves the DD ratio (1.44 vs
# 1.56 vs SPY). Baseball cadence.
WF_REBALANCE_MONTHS = 3
# League simulation starts this many days before the first rebalance so the
# 90-day probation is fully warm at WF_START (otherwise the first quarter of
# the walk would hold cash while every name is a fresh Minor).
WF_SIM_WARMUP_DAYS = 120
# U3 is approximated as satisfied inside the walk-forward (documented in
# DESIGN.md §10): SP500/$50B+ names trade >> 100K shares/day structurally,
# and historical volume is not in the store. The LIVE pipeline enforces U3
# with real volume data.
WF_ASSUME_LIQUID = True
