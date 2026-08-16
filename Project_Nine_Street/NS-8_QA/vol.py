"""vol.py — NS-8 ex-ante volatility estimate (R8, MOP 2012 §2.4).

EWMA variance with center-of-mass 60 trading days, annualized to 261 trading
days. Applied at t-1 to position at t (no look-ahead) — see walkforward.py /
signals.py for the no-lookahead application. Pure functions, stdlib only.
"""
from typing import List, Optional

import config

DELTA = config.VOL_DELTA        # δ/(1-δ) = 60 trading days center of mass
ANN = config.VOL_ANN            # trading days/year


def ewma_var(daily_returns: List[float], delta: Optional[float] = None) -> Optional[float]:
    """Ex-ante annualized variance (MOP eq. 1) from oldest-first daily returns.

    σ_t² = ANN · Σ (1-δ) δ^i (r_{t-1-i} - r̄_t)²  (exponentially weighted)
    Returns None if there aren't enough observations to estimate.
    """
    delta = delta or DELTA
    if not daily_returns:
        return None
    n = len(daily_returns)
    if n < 3:                    # too few obs for a stable variance
        return None
    w = [(1 - delta) * (delta ** (n - 1 - i)) for i in range(n)]
    wsum = sum(w)
    mean_r = sum(wi * ri for wi, ri in zip(w, daily_returns)) / wsum
    var = sum(wi * (ri - mean_r) ** 2 for wi, ri in zip(w, daily_returns)) / wsum
    return ANN * var


def exante_vol(daily_returns: List[float], delta: Optional[float] = None) -> Optional[float]:
    """Annualized ex-ante volatility (sqrt of EWMA var), or None if no estimate."""
    v = ewma_var(daily_returns, delta)
    return v ** 0.5 if v is not None and v >= 0 else None
