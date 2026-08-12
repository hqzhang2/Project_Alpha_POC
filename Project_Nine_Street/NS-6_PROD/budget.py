"""
budget.py — Drawdown budget tracking for NS-6.

Pure computation, no external API calls, no I/O. All inputs are pre-fetched
price histories passed by the caller.

Contract (frontier-specified):
  compute_drawdown(price_history)              → current_drawdown_pct (negative)
  compute_spy_drawdown(spy_prices)             → spy_drawdown_pct (negative)
  compute_budget(spy_dd_pct, theta)            → budget_pct (negative)
  budget_remaining(current_dd, budget, theta)  → budget_remaining_pct [0,1]

Fail-open: empty history → 0 drawdown (no drawdown = no risk signal).
"""

import logging

import config

log = logging.getLogger("ns6.budget")


def compute_drawdown(price_history) -> float:
    """Current drawdown as a negative fraction (e.g. -0.031 = down 3.1%).

    drawdown = (price / running_peak) - 1, at the LAST bar.
    Empty/short history → 0.0 (no drawdown, fail-open).
    """
    if not price_history:
        return 0.0
    peak = 0.0
    last = 0.0
    for p in price_history:
        if p is None:
            continue
        last = p
        if p > peak:
            peak = p
    if peak <= 0:
        return 0.0
    return (last / peak) - 1.0


def compute_spy_drawdown(spy_prices) -> float:
    """SPY benchmark drawdown (negative fraction). Same math as compute_drawdown."""
    return compute_drawdown(spy_prices)


def compute_budget(spy_dd_pct, theta=None) -> float:
    """Drawdown budget as a negative fraction.

    budget = min(spy_dd_pct * spy_dd_ratio, -absolute_floor_bps/10000)

    The absolute floor guarantees you ALWAYS have at least 5% budget
    (drawdown rope), regardless of how shallow SPY's drawdown is.

    If SPY is down 8%, half = 4%, floor = 5% → budget = 5% (floor applies).
    If SPY is down 20%, half = 10%, floor = 5% → budget = 10% (half applies).

    FRONTIER RESOLVED: max() vs min() — min() is correct. Both values are
    negative, so min() picks the larger magnitude (more budget = more rope).
    The floor guarantees you ALWAYS have at least 5% drawdown tolerance,
    even when SPY's drawdown is trivial. When SPY drawdown is deep, the
    half-rule dominates. This matches the design doc intent.
    """
    theta = theta or config.load_theta()
    b = theta["budget"]
    ratio = b["spy_dd_ratio"]
    floor = -b["absolute_floor_bps"] / 10000.0
    if spy_dd_pct is None:
        # No SPY data — use the floor as a conservative budget.
        return floor
    computed = spy_dd_pct * ratio
    return min(computed, floor)  # more negative = larger budget (floor guarantee)


def budget_remaining(current_dd_pct, budget_pct, theta=None) -> float:
    """Fraction of drawdown budget remaining, in [0, 1].

    Both current_dd_pct and budget_pct are NEGATIVE fractions.
    budget_remaining = 1 - (current_dd / budget)

    Example: current_dd=-1.3%, budget=-5% → 1 - (0.26) = 0.74 (74% left).
    current_dd >= budget (drew down the full budget) → 0.0.
    Budget of 0 (degenerate) → 1.0 (fail-open, no constraint).
    """
    theta = theta or config.load_theta()
    if budget_pct is None or budget_pct == 0:
        return 1.0
    if current_dd_pct is None:
        return 1.0
    # normalize: work with negative magnitudes
    consumed = current_dd_pct / budget_pct  # ratio of magnitudes (both negative)
    remaining = 1.0 - consumed
    # clamp to [0, 1]; if consumed is negative (no drawdown yet), remaining > 1 → clamp
    return max(0.0, min(1.0, remaining))


def status_snapshot(portfolio_prices, spy_prices, theta=None):
    """Convenience: compute all budget fields from price histories.

    Returns dict suitable for /api/enforcement/status budget block.
    Fail-open: any missing input → best-effort defaults.
    """
    theta = theta or config.load_theta()
    current_dd = compute_drawdown(portfolio_prices)
    spy_dd = compute_spy_drawdown(spy_prices)
    budget = compute_budget(spy_dd, theta)
    remaining = budget_remaining(current_dd, budget, theta)
    return {
        "current_drawdown_pct": round(current_dd, 4),
        "spy_drawdown_pct": round(spy_dd, 4),
        "budget_pct": round(budget, 4),
        "budget_remaining_pct": round(remaining, 4),
    }
