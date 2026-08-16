"""config.py — NS-8 Tactical Asset Allocation Service Configuration.

All thresholds live here so the walk-forward harness and the live server share
one source of truth. Nothing hardcoded downstream.
"""
import os
from pathlib import Path

# ── Signal ──────────────────────────────────────────────────────────────
SMA_WINDOW = 200                    # 200-day SMA
REBALANCE_CADENCE = "monthly"       # signal frequency
TRANCHES = 4                        # weekly tranching
TRANCHE_WEEK = [1, 2, 3, 4]         # which week each tranche rebalances

# ── Service ─────────────────────────────────────────────────────────────
PORT = int(os.environ.get("PORT", 9281))    # QA; PROD 9280

# ── Universe ────────────────────────────────────────────────────────────
RISKY_ASSETS = ["SPY", "EFA", "IEF", "VNQ", "DBC"]
CASH_PROXY = "SHV"
ASSET_WEIGHT = 0.20                 # 20% each when in-trend

# ── Costs ───────────────────────────────────────────────────────────────
TXN_COST_BPS = 10                   # per round-trip

# ── Data ────────────────────────────────────────────────────────────────
DATA_SOURCE = "yfinance"            # default; set "polygon" for adjusted-close accuracy
POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")
LOOKBACK_DAYS = 252 + SMA_WINDOW    # enough for warm SMA

# ── Paths ───────────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "ns8.db"
SIGNALS_PATH = DATA_DIR / "signals.json"
TRANCHE_STATE_PATH = DATA_DIR / "tranche_state.json"
AUDIT_LOG_PATH = DATA_DIR / "audit_log.jsonl"

# ── Walk-Forward Harness ────────────────────────────────────────────────
WF_START = "2006-01-01"
WF_END = "2026-07-31"
WF_REBALANCE_MONTHS = 1             # monthly signal generation

# ── IBKR (paper only) ───────────────────────────────────────────────────
# Credentials via env vars / Vault — NEVER hardcode
# Note: ib_async authenticates via running Gateway/TWS (port 7497 paper).
# IBKR username/password are for Gateway/TWS login, not this API.
IBKR_HOST = os.environ.get("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(os.environ.get("IBKR_PORT", "7497"))       # paper
IBKR_CLIENT_ID = int(os.environ.get("IBKR_CLIENT_ID", "987654321"))
IBKR_ACCOUNT = os.environ.get("IBKR_ACCOUNT", "DUR906177")