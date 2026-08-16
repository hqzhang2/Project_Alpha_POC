"""config.py — NS-X Strategy Allocation Service Configuration.

All thresholds live here so the allocator, rotation engine, and server share
one source of truth. Nothing hardcoded downstream. Mirrors the NS-6/7/8 house
pattern.
"""
import os
from pathlib import Path

# ── Service ──────────────────────────────────────────────────────────────
PORT = int(os.environ.get("PORT", 9291))    # QA; PROD 9290

# ── Rotation signal (risk-adjusted relative momentum) ───────────────────
ROTATION = "relative_momentum_risk_adjusted"
RISK_ADJUST = True              # vol-normalize returns before momentum (mandatory)
MOM_LOOKBACK_DAYS = 126         # skip-month momentum lookback (NS-7 param)
MOM_SKIP_DAYS = 21              # skip-month momentum skip (NS-7 param)

# ── Vol normalization (MOP 2012 §2.4, mirrors NS-8 vol.py) ───────────────
VOL_DELTA = 60 / 61             # EWMA center-of-mass = 60 trading days
VOL_ANN = 261                   # trading days/year

# ── Weighting / caps / floors ────────────────────────────────────────────
NSX_MAX_STRATEGY_W = 0.40       # max single risky-strategy weight
NSX_MIN_SLEEVE = 0.03           # optional floor on any positive-momentum strategy
NSX_DEFENSIVE_FLOOR = 0.10      # defensive-role strategies never go below this
CASH_STRATEGY_ID = "cash"       # residual risk-off sleeve
MAX_BOOK_TURNS_PER_YEAR = 2.0   # strategy-level rotation turnover cap
NSX_STALE_DAYS = 5              # alloc stale age before equal-weight fallback

# ── Composed-book security guards (applied by NS-5, §6.3) ────────────────
COMPOSED_MAX_NAME_W = 0.08      # per-name cap after overlap
COMPOSED_MAX_SECTOR_W = 0.40    # sector/β cap
COMPOSED_MIN_EFF_N = 15         # baseball effective-N floor

# ── Paths ────────────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).resolve().parent / "data"
ALLOC_PATH = DATA_DIR / "strategy_alloc.json"
DB_PATH = DATA_DIR / "nsx.db"
STORE_DIR = Path(__file__).resolve().parent.parent  # repo root (cross-service reads)

# ── Cross-service read paths (decoupled, house pattern) ─────────────────
NS8_HIST = Path("/Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-8_QA/data/ns8_hist_closes.json")
NS7_SELECTION = Path("/Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-7_QA/data/selection.json")
