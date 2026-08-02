from . import config
from . import data
from . import indicators
from . import utils
from . import risk

# ── Re-exports (P6 remediation) ─────────────────────────────────────────────
# Services import `from common import fit_hmm, rsi, macd, ...` (e.g. NS-3/NS-4
# PROD backends). These names live in submodules; re-export at package level
# so `from common import X` works without touching every importer.
from .config import get_ns_config
from .data import get_yahoo_client, get_etf_holdings
from .indicators import (
    sma, ema, rsi, macd, bollinger_bands, bb_position,
    adx, atr, obv, obv_slope, fit_hmm, compute_all,
)
