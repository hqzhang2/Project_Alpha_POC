"""
enforcement.py — Drawdown enforcement: exposure multiplier, circuit breakers,
position stops, re-entry hysteresis.

Implements the frontier specification (see module docstring / design doc §3).
Pure functions — no external API calls, no I/O. All inputs pre-computed by
the caller. All thresholds from config.py theta dict — never hardcode.

Phase 1: compute_exposure_multiplier() (linear budget-only).
Phase 2: compute_exposure_multiplier_v2() (multi-signal graduated).
Phase 4: check_circuit_breakers(), check_position_stops(),
         check_reentry_hysteresis().
"""

import logging
from datetime import datetime, timedelta
from typing import List, Optional

import config

log = logging.getLogger("ns6.enforcement")

_PHASE2_SIGNAL_KEYS = ("vol_ratio", "corr_sign", "vix_level", "vix_trend")


# --------------------------------------------------------------------------- #
# Exposure multiplier
# --------------------------------------------------------------------------- #
def compute_exposure_multiplier(budget_remaining_pct, theta=None) -> float:
    """Phase 1: linear budget-only multiplier.

    multiplier = budget_remaining_pct, clamped to [hard_floor, 1.0].
    """
    theta = theta or config.load_theta()
    hard_floor = theta["budget"]["hard_floor"]
    if budget_remaining_pct is None:
        budget_remaining_pct = 1.0  # fail-open: unknown budget → full exposure
    return max(hard_floor, min(1.0, budget_remaining_pct))


def _count_tiers(vol_ratio, corr_sign, vix_level, vix_trend, theta) -> int:
    """Count active signal tiers (0-3). None → treated as not contributing."""
    st = theta["multiplier"]["signal_thresholds"]
    tiers = 0
    if vol_ratio is not None and vol_ratio > st["vol_ratio"]:
        tiers += 1
    if corr_sign is not None and corr_sign > 0:
        tiers += 1
    if (vix_level is not None and vix_trend is not None
            and vix_level > st["vix_level"] and vix_trend > 0):
        tiers += 1
    return tiers


def compute_exposure_multiplier_v2(budget_remaining_pct, regime, vol_ratio,
                                   corr_sign, vix_level, vix_trend,
                                   theta=None,
                                   signals_age_days: Optional[dict] = None) -> float:
    """Phase 2: multi-signal graduated multiplier.

    multiplier = base * regime_factor - tier_deduction * tiers, clamped.

    signals_age_days: optional dict {signal_key: days_stale}. Any signal
    older than theta staleness_days adds a phantom tier.
    """
    theta = theta or config.load_theta()
    m = theta["multiplier"]
    hard_floor = theta["budget"]["hard_floor"]

    # Fail-open: unknown regime → R1 (no penalty for data gaps)
    rbf = m["regime_budget_factors"]
    regime_factor = rbf.get(regime if regime else "R1", rbf["R1"])

    if budget_remaining_pct is None:
        budget_remaining_pct = 1.0
    base = budget_remaining_pct

    tiers = _count_tiers(vol_ratio, corr_sign, vix_level, vix_trend, theta)
    # Cap tiers to max_tiers.
    tiers = min(tiers, m["max_tiers"])

    # Staleness penalty: phantom tier if any signal is stale.
    if signals_age_days:
        stale_threshold = m["staleness_days"]
        if any(age is not None and age > stale_threshold
               for age in signals_age_days.values()):
            tiers += 1
            log.warning("stale signal penalty applied (tiers=%d)", tiers)

    tier_penalty = m["tier_deduction"] * tiers
    multiplier = base * regime_factor - tier_penalty
    return max(hard_floor, min(1.0, multiplier))


# --------------------------------------------------------------------------- #
# Circuit breakers & position stops
# --------------------------------------------------------------------------- #
def check_circuit_breakers(current_drawdown_pct, budget_pct,
                           position_drawdowns=None,
                           cross_sectional_corr=None, theta=None) -> List[dict]:
    """Return list of breaker dicts (one per type), each with triggered flag."""
    theta = theta or config.load_theta()
    cb = theta["circuit_breakers"]
    out = []

    # HARD FLOOR
    if current_drawdown_pct is not None and budget_pct is not None:
        # FRONTIER RESOLVED: the spec's `dd >= trigger*abs(budget)` was a sign
        # bug (negative vs positive comparison, could never fire). Correct:
        # triggered when |dd| >= trigger * |budget| → dd <= -(trigger*|budget|).
        threshold = cb["hard_floor_trigger"] * abs(budget_pct)
        floor_level = -threshold  # negative breach level
        triggered = current_drawdown_pct <= floor_level
        out.append({
            "type": "hard_floor",
            "triggered": bool(triggered),
            "detail": (f"current_dd={current_drawdown_pct:.4f} "
                       f"<= floor={floor_level:.4f}"),
            "action": "Set multiplier to hard_floor, liquidate within 2 trading days.",
        })
    else:
        out.append({"type": "hard_floor", "triggered": False,
                    "detail": "missing drawdown/budget", "action": ""})

    # SYSTEMIC EVENT
    if position_drawdowns is not None and cross_sectional_corr is not None:
        se = cb["systemic_event"]
        total = len(position_drawdowns)
        n_breached = sum(1 for d in position_drawdowns.values()
                         if d is not None and d < se["pct_threshold"])
        pct_breached = (n_breached / total) if total else 0.0
        triggered = (pct_breached >= se["pct_positions"]
                     and cross_sectional_corr > se["corr_threshold"])
        out.append({
            "type": "systemic_event",
            "triggered": bool(triggered),
            "detail": (f"{n_breached}/{total} positions < {se['pct_threshold']:.2f} "
                       f"(pct={pct_breached:.2f}), corr={cross_sectional_corr:.2f}"),
            "action": ("Systemic breakdown. Floor at hard_floor. Do not re-enter "
                       "until hysteresis clears."),
        })
    else:
        out.append({"type": "systemic_event", "triggered": False,
                    "detail": "missing position_drawdowns/corr", "action": ""})

    return out


def check_position_stops(position_drawdowns, asset_classes=None,
                         theta=None) -> List[dict]:
    """Per-position drawdown stop check.

    position_drawdowns: {ticker: drawdown_pct (negative)}
    asset_classes: {ticker: "equity"|"bond_etf"|"commodity_etf"|"cash_proxy"}
                   default "unknown" if absent.
    """
    theta = theta or config.load_theta()
    ps = theta["position_stops"]
    reentry_days = theta["hysteresis"]["position_stop_reentry_days"]
    asset_classes = asset_classes or {}
    out = []
    for ticker, dd in (position_drawdowns or {}).items():
        cls = asset_classes.get(ticker, "unknown")
        threshold = ps.get(cls, ps["unknown"])
        triggered = dd is not None and dd <= threshold
        out.append({
            "ticker": ticker,
            "drawdown": dd,
            "threshold": threshold,
            "asset_class": cls,
            "triggered": bool(triggered),
            "action": (f"Exit {ticker}, proceeds to BIL. No re-entry for "
                       f"{reentry_days} trading days."),
        })
    return out


def check_reentry_hysteresis(last_breaker_time, last_stop_times,
                             current_time=None, theta=None) -> bool:
    """Return True if re-entry is BLOCKED (within a hysteresis window).

    last_breaker_time: datetime | None — most recent circuit breaker.
    last_stop_times: {ticker: datetime} | None — most recent position stops.
    current_time: datetime | None — defaults to now.
    """
    theta = theta or config.load_theta()
    hy = theta["hysteresis"]
    now = current_time or datetime.now()

    if last_breaker_time is not None:
        window = timedelta(days=hy["breaker_reentry_days"] * 5 / 7)
        if now - last_breaker_time < window:
            return True

    if last_stop_times:
        window = timedelta(days=hy["position_stop_reentry_days"] * 5 / 7)
        for t in last_stop_times.values():
            if t is not None and now - t < window:
                return True

    return False
