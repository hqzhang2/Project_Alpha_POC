"""vol.py — NS-X ex-ante volatility (mirrors NS-8 vol.py, MOP 2012 §2.4).

EWMA variance with center-of-mass 60 trading days, annualized to 261 days.
Pure, stdlib-only, no look-ahead (applied at t-1 to position at t). Used to
vol-normalize each strategy's return stream before momentum ranking, so scores
are comparable across strategies of very different volatility.
"""
from typing import List, Optional

import config

DELTA = config.VOL_DELTA        # δ/(1-δ) = 60 trading days center of mass
ANN = config.VOL_ANN            # trading days/year


def ewma_var(daily_returns: List[float], delta: Optional[float] = None) -> Optional[float]:
    """Ex-ante annualized variance (MOP eq. 1) from oldest-first daily returns."""
    delta = delta or DELTA
    if not daily_returns:
        return None
    n = len(daily_returns)
    if n < 3:
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
