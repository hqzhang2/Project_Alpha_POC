"""rotation.py — NS-X rotation signal: risk-adjusted relative momentum.

Design §5. Core pure logic (unit-testable, no I/O):
  1. Vol-normalize each strategy's live return series (return/σ, MOP §2.4).
  2. Compute skip-month momentum on the vol-normalized series (126/21, NS-7 params).
  3. Weight by relative momentum vs the cross-sectional median (overweight only
     above-median), with:
       - quality floor (negative momentum / no stream → 0)
       - concentration cap (max 0.40)
       - defensive floor (role-gated, never zeroed — anti-procyclical §5.2)
       - cash residual (w_cash = 1 − Σ risky), full risk-off when all floor
  4. Normalize to sum 1.0, long-only.

All pure functions; allocator.py wires I/O around this.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import config
import vol


# ── Vol-normalized momentum ──────────────────────────────────────────────
_EPS_SIGMA = 1e-12   # treat ex-ante vol below this as zero (float residue guard)


def normalized_returns(daily_returns: List[float]) -> List[float]:
    """Daily returns scaled by ex-ante vol (return/σ). Zero-vol → flat (momentum 0)."""
    sigma = vol.exante_vol(daily_returns)
    if sigma is None or sigma <= _EPS_SIGMA:
        return [0.0] * len(daily_returns)   # zero/near-zero-vol → flat (momentum 0)
    return [r / sigma for r in daily_returns]


def skip_month_momentum(daily_returns: List[float],
                        lookback: Optional[int] = None,
                        skip: Optional[int] = None) -> Optional[float]:
    """Skip-month momentum = SUM of vol-normalized returns over the window.

    mom = Σ_{t−lookback..t−skip−1} (r/σ)   — the risk-adjusted return over the
    lookback window, skipping the most recent `skip` days (short-term-reversal
    contamination, NS-7 convention). Summing (not compounding a product) keeps
    it a well-scaled, comparable risk-adjusted return across strategies and
    avoids numerical explosion. Returns None if insufficient history.
    """
    lookback = lookback or config.MOM_LOOKBACK_DAYS
    skip = skip or config.MOM_SKIP_DAYS
    if len(daily_returns) < lookback + 1:
        return None
    norm = normalized_returns(daily_returns)
    # window: indices [t−lookback, t−skip−1]
    t = len(norm) - 1
    lo = t - lookback
    hi = t - skip          # exclusive upper bound
    if lo < 0 or hi <= lo:
        return None
    window = norm[lo:hi]
    if not window:
        return None
    return sum(window)


def strategy_momentum(daily_returns: List[float]) -> Optional[float]:
    """Full risk-adjusted momentum for one strategy's live return series."""
    if not daily_returns or len(daily_returns) < 3:
        return None
    return skip_month_momentum(daily_returns)


# ── Weighting ────────────────────────────────────────────────────────────
def weight_strategies(scores: Dict[str, Optional[float]],
                      roles: Dict[str, str]) -> Dict[str, float]:
    """Map {strategy_id: risk-adj momentum} + {id: role} → {id: weight}.

    Implements §5.2, in explicit passes (correctness — order is not cosmetic):

      P0  risky = enabled strategies with a role != "riskoff"
      P1  ABSOLUTE quality floor: momentum < 0 or None → weight 0.
          (A strategy must have POSITIVE risk-adjusted momentum to earn weight.)
      P2  RELATIVE tilt among survivors (mom ≥ 0): w ∝ max(mom − median, 0).
      P2.5 MIN-SLEEVE floor: any positive-momentum strategy keeps ≥ NSX_MIN_SLEEVE
          (so a valid signal never gets a de-minimis allocation).
      P3  DEFENSIVE floor (anti-procyclical): every "defensive" strategy keeps
          ≥ NSX_DEFENSIVE_FLOOR even when its momentum is negative (overrides P1).
      P4  Concentration cap: risky weight ≤ NSX_MAX_STRATEGY_W.
      P5  Cash residual: w_cash = 1 − Σ risky (≥ 0). All-negative → full risk-off
          (only defensive ballast + cash).

    Returns a fully-invested, long-only dict summing to 1.0.
    """
    risky = [k for k, v in roles.items() if roles.get(k) != "riskoff"]
    risky = [k for k in risky if k in scores]
    if not risky:
        return {config.CASH_STRATEGY_ID: 1.0}

    def _role(k):
        return roles.get(k, "")

    # P1 + P2: absolute quality floor then relative tilt among survivors
    weights: Dict[str, float] = {}
    pos: Dict[str, float] = {}
    for k in risky:                                   # absolute floor: mom > 0
        m = scores.get(k)
        if m is not None and m > 0:
            pos[k] = float(m)
    if pos:
        median = sorted(pos.values())[len(pos) // 2]
        raw = {k: max(m - median, 0.0) for k, m in pos.items()}
        s = sum(raw.values())
        if s > 0:
            for k, m in raw.items():
                weights[k] = m / s
        else:                                               # all tied at median
            for k in pos:
                weights[k] = 1.0 / len(pos)
    # strategies with non-positive momentum get 0 unless defensive (P3)
    for k in risky:
        if k not in weights and _role(k) != "defensive":
            weights[k] = 0.0

    # P2.5: min-sleeve floor — a valid positive-momentum strategy is never de-minimis
    for k in pos:
        if weights[k] < config.NSX_MIN_SLEEVE:
            weights[k] = config.NSX_MIN_SLEEVE

    # P3: defensive floor — never zero a defensive strategy
    for k in risky:
        if _role(k) == "defensive":
            weights[k] = max(weights.get(k, 0.0), config.NSX_DEFENSIVE_FLOOR)

    # P4: concentration cap
    for k in risky:
        weights[k] = min(weights.get(k, 0.0), config.NSX_MAX_STRATEGY_W)

    # P5: cash residual (renormalize risky if floors push over 1.0)
    risky_sum = sum(weights.get(k, 0.0) for k in risky)
    if risky_sum > 1.0:
        for k in risky:
            weights[k] = weights.get(k, 0.0) / risky_sum
        risky_sum = 1.0
    weights[config.CASH_STRATEGY_ID] = round(max(0.0, 1.0 - risky_sum), 12)
    for k in risky:
        weights[k] = round(weights.get(k, 0.0), 12)
    return weights


def strategy_turnover(prev: Dict[str, float], curr: Dict[str, float]) -> float:
    """Half the L1 distance between two weight vectors (fraction of book traded)."""
    keys = set(prev) | set(curr)
    return 0.5 * sum(abs(prev.get(k, 0.0) - curr.get(k, 0.0)) for k in keys)


def compute_allocation(return_streams: Dict[str, List[float]],
                       roles: Dict[str, str]) -> Dict[str, float]:
    """Full allocation: momentum → weights, for the allocator."""
    scores = {sid: strategy_momentum(ret) for sid, ret in return_streams.items()}
    return weight_strategies(scores, roles)
