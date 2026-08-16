"""vol.py — NS-X ex-ante volatility (re-exports common.risk.vol).

The EWMA math lives in `common.risk.vol` (single source of truth, shared with
NS-8). This module keeps the NS-X-facing names (`ewma_var`, `exante_vol`,
`DELTA`, `ANN`) wired to NS-X's config thresholds so downstream imports are
unchanged.
"""
import sys
from pathlib import Path

import config

# make the repo root importable so `common.risk.vol` resolves (decoupled read;
# mirrors the NS-5 sys.path pattern, no cross-service module import of config).
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.risk import vol as _vol  # noqa: E402

DELTA = config.VOL_DELTA        # δ/(1-δ) = 60 trading days center of mass
ANN = config.VOL_ANN            # trading days/year


def ewma_var(daily_returns, delta=None):
    return _vol.ewma_var(daily_returns, delta if delta is not None else DELTA, ANN)


def exante_vol(daily_returns, delta=None):
    return _vol.exante_vol(daily_returns, delta if delta is not None else DELTA, ANN)
