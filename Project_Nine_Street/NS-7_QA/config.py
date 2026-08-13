"""config.py — NS-7 Growth/Momentum Selection service configuration.

All thresholds live here so the walk-forward harness and the live server share
one source of truth (the NS-6 config.py pattern). Nothing hardcoded downstream.
"""
import os

# ── Service identity ────────────────────────────────────────────────────
PORT = int(os.environ.get("PORT", 9271))          # QA; PROD 9270 (reserved)
ENV = os.environ.get("ENV", "QA")

# ── Universe eligibility (§3.1 of DESIGN.md) ────────────────────────────
MARKET_CAP_MIN = 50_000_000_000.0                 # U2: > $50B
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

# ── Guardrail caps (§5) ─────────────────────────────────────────────────
MAX_POSITION_WEIGHT = 0.08                          # G4: 8% per name
MIN_EFFECTIVE_N = 15                                # G4: min effective-N
MAX_SECTOR_WEIGHT = 0.40                            # G4: 40% sector cap

# ── Data path (module-relative, like NS-6 store.py) ────────────────────
import pathlib
DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "ns7.db"
