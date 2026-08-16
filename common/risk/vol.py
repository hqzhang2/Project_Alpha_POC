"""common.risk.vol — shared ex-ante volatility (MOP 2012 §2.4).

EWMA variance with a configurable center-of-mass, annualized. Pure stdlib, no
service config import — callers pass `delta` and `ann` (or use the defaults).
This is the single source of truth for ex-ante vol across NS-8 (R8 inverse-vol
sizing) and NS-X (risk-adjusted momentum), replacing the two byte-identical
`NS-*_QA/vol.py` copies.

delta default: 60/61 → 60-trading-day center of mass (MOP convention).
ann  default: 261 trading days/year.
"""
from typing import List, Optional

DEFAULT_DELTA = 60 / 61
DEFAULT_ANN = 261


def ewma_var(daily_returns: List[float],
             delta: Optional[float] = None,
             ann: Optional[float] = None) -> Optional[float]:
    """Ex-ante annualized variance (MOP eq. 1) from oldest-first daily returns.

    σ_t² = ANN · Σ (1-δ) δ^i (r_{t-1-i} - r̄_t)²  (exponentially weighted).
    Returns None if there aren't enough observations to estimate.
    """
    delta = delta if delta is not None else DEFAULT_DELTA
    ann = ann if ann is not None else DEFAULT_ANN
    if not daily_returns:
        return None
    n = len(daily_returns)
    if n < 3:
        return None
    w = [(1 - delta) * (delta ** (n - 1 - i)) for i in range(n)]
    wsum = sum(w)
    mean_r = sum(wi * ri for wi, ri in zip(w, daily_returns)) / wsum
    var = sum(wi * (ri - mean_r) ** 2 for wi, ri in zip(w, daily_returns)) / wsum
    return ann * var


def exante_vol(daily_returns: List[float],
               delta: Optional[float] = None,
               ann: Optional[float] = None) -> Optional[float]:
    """Annualized ex-ante volatility (sqrt of EWMA var), or None if no estimate."""
    v = ewma_var(daily_returns, delta, ann)
    return v ** 0.5 if v is not None and v >= 0 else None
