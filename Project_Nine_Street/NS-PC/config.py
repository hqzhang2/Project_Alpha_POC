"""config.py — NS-PC Portfolio Constructor configuration.

Reads NS-X/NS-5/NS-8 targets, composes the fund book, materializes whole-share
positions into paper_portfolio.json. Mirrors the NS-6/7/8/X house pattern.
"""
import os
from pathlib import Path

# ── Service ──────────────────────────────────────────────────────────────
PORT = int(os.environ.get("PORT", 9301))    # QA; PROD 9300

# ── Composed-book guards (NS-X §6.3, enforced here) ─────────────────────
COMPOSED_MAX_NAME_W = 0.08      # per-name cap after composition
COMPOSED_MAX_SECTOR_W = 0.40    # sector/β cap
COMPOSED_MIN_EFF_N = 15         # baseball effective-N floor

# ── Cash proxy ───────────────────────────────────────────────────────────
CASH_PROXY = "BIL"              # cash-equivalent position (PM decision)
CASH_STRATEGY_ID = "cash"       # NS-X strategy id that maps to the cash proxy

# ── Portfolio materialization ────────────────────────────────────────────
INITIAL_BALANCE = 100000.0
COMMISSION_PER_SHARE = 0.0      # paper book; no commission model yet
STRATEGY_LABEL = "NS-X-fund"    # retires "NS-Capital-Preservation"

# ── Paths (decoupled reads, house pattern) ───────────────────────────────
_DIR = Path(__file__).resolve().parent
ROOT = _DIR.parent.parent                      # repo root
NSX_ALLOC = _DIR.parent / "NS-X_QA" / "data" / "strategy_alloc.json"
NS5_BLEND = _DIR.parent / "NS-5_QA" / "data" / "sleeve_blend.json"
NS8_SIGNALS = _DIR.parent / "NS-8_QA" / "data" / "signals.json"
PORTFOLIO_PATH = _DIR.parent / "scripts" / "paper_portfolio.json"
DATA_DIR = _DIR / "data"

# ── Staleness ────────────────────────────────────────────────────────────
STALE_DAYS = 5                  # an input older than this → fail-open (no write)

# ── Tickers that must NOT be double-counted across sleeves ───────────────
# NS-8's SPY/EFA etc. overlap NS-7's large-caps; the per-name cap handles the
# numeric weight, but these are the ETF books kept separate from equity names.
TACTICAL_ETFS = {"SPY", "EFA", "IEF", "VNQ", "DBC", "SHV", "BIL"}
