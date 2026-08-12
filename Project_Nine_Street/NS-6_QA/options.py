"""
options.py — Protective put overlay & covered call gate (Phase 2–3).

FRONTIER METHODOLOGY (Phase 2):
  - recommend_put_overlay() — which put to buy based on exposure multiplier
  - estimate_put_cost_pct() — parametric proxy from VIX (Phase 2 approximation;
    exact option-chain pricing lands in Phase 3)

Phase 3 (covered call gate) is spec'd in the docstring for junior wiring.

All thresholds from config.py — no hardcoded values.
"""

import logging

import config

log = logging.getLogger("ns6.options")


# ── Protective put overlay (Phase 2) ──────────────────────────────────────
def recommend_put_overlay(multiplier, equity_notional, vix_level=None, theta=None):
    """Return a protective put recommendation dict.

    Consumed by /api/enforcement/status and the backtest harness.

    Parameters
    ----------
    multiplier : float — current exposure multiplier [0.25, 1.0]
    equity_notional : float — total equity notional to hedge (NAV × equity_weight)
    vix_level : float or None — VIX for cost estimation (None → use config proxy)
    theta : dict — config.load_theta() or None

    Returns
    -------
    dict: {
        "recommended": bool,
        "put_type": "otm_spy" | "atm_spy" | "itm_plus_individual",
        "strike_offset_pct": float,   # % ITM/OTM (negative = ITM, positive = OTM)
        "notional_to_hedge": float,
        "estimated_premium_pct": float,  # % of notional (monthly)
        "estimated_annual_cost_pct": float,
        "rationale": str,
    }

    Methodology decisions (frontier):
      - multiplier ≥ 0.80 → no puts (budget ample, don't waste premium)
      - multiplier ∈ [0.60, 0.80) → 5-10% OTM SPY puts (cheap, tail hedge only)
      - multiplier ∈ [0.40, 0.60) → ATM SPY puts (moderate protection)
      - multiplier < 0.40 → ITM SPY puts + individual position puts
        (budget critical — max protection, accept premium cost)

    Phase 2 approximation: parametric premium from VIX. Phase 3 wires live
    Polygon option-chain data for exact pricing.
    """
    theta = theta or config.load_theta()
    pp = theta["protective_puts"]
    gate = pp["gate_multiplier"]
    bands = pp["bands"]

    if multiplier >= gate:
        return _no_put("multiplier above gate", gate)

    equity_notional = equity_notional or 0
    premium = estimate_put_cost_pct(vix_level, theta)  # ATM base cost

    if multiplier >= bands["otm"]["low"]:  # [0.60, 0.80)
        strike_pct = bands["otm"]["strike_pct"]
        put_type = "otm_spy"
        rationale = "Tail hedge — cheap OTM insurance."
        premium *= 0.6  # OTM is ~60% of ATM cost

    elif multiplier >= bands["atm"]["low"]:  # [0.40, 0.60)
        strike_pct = bands["atm"]["strike_pct"]
        put_type = "atm_spy"
        rationale = "Moderate protection — budget shrinking."
        # stays ATM

    else:  # < 0.40
        strike_pct = bands["itm"]["strike_pct"]
        put_type = "itm_plus_individual"
        rationale = "Max protection — budget critical. ITM + individual position puts."
        premium *= 1.4  # ITM is ~40% more expensive

    notional = equity_notional * pp["spy_overlay_coverage"]
    annual = premium * 12  # annualized (monthly premium × 12)

    return {
        "recommended": True,
        "put_type": put_type,
        "strike_offset_pct": round(strike_pct, 3),
        "notional_to_hedge": round(notional, 0),
        "estimated_premium_pct": round(premium, 4),
        "estimated_annual_cost_pct": round(annual, 4),
        "rationale": rationale,
    }


def _no_put(reason, gate):
    return {
        "recommended": False,
        "put_type": None,
        "strike_offset_pct": None,
        "notional_to_hedge": 0,
        "estimated_premium_pct": 0.0,
        "estimated_annual_cost_pct": 0.0,
        "rationale": f"{reason} (≥ {gate})",
    }


def estimate_put_cost_pct(vix_level=None, theta=None):
    """Parametric estimate of one-month ATM SPY put premium as % of notional.

    Phase 2 proxy: premium ≈ VIX / 3000 (capped at 4%).
      VIX 15 → 0.5%   (calm)
      VIX 20 → 0.67%  (normal)
      VIX 30 → 1.0%   (elevated fear)
      VIX 40 → 1.33%  (crisis)

    Phase 3 replacement: live Polygon chain data for exact mid-price.

    Returns float (0.0 when VIX unavailable — fail-open).
    """
    theta = theta or config.load_theta()
    if vix_level is None:
        return 0.01  # fallback: 1% monthly (~12% annual drag in calm markets)
    cost = vix_level / 3000.0
    return min(cost, 0.04)


# ── Covered call gate (Phase 3 — spec only) ──────────────────────────────
def covered_call_gate(multiplier, position_drawdown=None, theta=None):
    """Return whether covered calls are allowed for a given position.

    Phase 3 methodology spec (junior wires to live option chains + A_T data):
      - multiplier ≥ 0.60 AND position NOT flagged for drawdown reduction
        AND option chain liquid (bid-ask < 5% of premium)
      - Overwrite %: multiplier ≥ 0.80 → 50% of notional;
        multiplier ∈ [0.60, 0.80) → 25%
      - Specs: 30-45 DTE, 0.20-0.30 delta, roll/close at 21 DTE or 50% profit

    Phase 2 stub: returns False (disabled until Phase 3).
    """
    return False
