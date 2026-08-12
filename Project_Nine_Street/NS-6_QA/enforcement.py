"""
enforcement.py — Drawdown enforcement: exposure multiplier, circuit breakers,
position stops, re-entry hysteresis.

FRONTIER SPECIFICATION (this docstring is the contract — junior implements,
must NOT change formulas or thresholds).

────────────────────────────────────────────────────────────────────────
PHASE 1: Linear budget-only multiplier
────────────────────────────────────────────────────────────────────────

compute_exposure_multiplier(budget_remaining_pct, theta) → float

  budget_remaining_pct ∈ [0.0, 1.0] — fraction of drawdown budget remaining

  Phase 1 formula:
    multiplier = budget_remaining_pct  (linear, 1:1 mapping)

  Clamp:
    multiplier = max(theta["budget"]["hard_floor"], min(1.0, multiplier))

  Examples:
    100% budget remaining → 1.00
    60%  budget remaining → 0.60
    25%  budget remaining → 0.25 (at floor)
    0%   budget remaining → 0.25 (floor overrides)

────────────────────────────────────────────────────────────────────────
PHASE 2: Multi-signal graduated multiplier
────────────────────────────────────────────────────────────────────────

compute_exposure_multiplier_v2(budget_remaining_pct, regime, vol_ratio,
                                corr_sign, vix_level, vix_trend, theta) → float

  INPUTS:
    budget_remaining_pct : float  — from budget.budget_remaining()
    regime               : str    — "R1"|"R2"|"R3"|"R4" from NS-5 regime axis
    vol_ratio            : float  — trailing_vol / long_run_vol from NS-5 drift
    corr_sign            : float  — stock_bond_corr from NS-5 regime (sign matters)
    vix_level            : float  — current VIX from A_T sentiment
    vix_trend            : float  — VIX daily change (positive = rising)

  COMPUTATION:

    1. Base multiplier (Phase 1 formula):
       base = budget_remaining_pct

    2. Regime budget factor from theta["multiplier"]["regime_budget_factors"]:
       regime_factor = regime_budget_factors[regime]

    3. Count active signal tiers:
       tiers = 0
       if vol_ratio > theta["multiplier"]["signal_thresholds"]["vol_ratio"]:
           tiers += 1
       if corr_sign > 0:  # positive stock-bond correlation = diversification failed
           tiers += 1
       if vix_level > theta["multiplier"]["signal_thresholds"]["vix_level"] \
          and vix_trend > 0:
           tiers += 1

    4. Effective multiplier:
       tier_penalty = theta["multiplier"]["tier_deduction"] * tiers
       multiplier = base * regime_factor - tier_penalty

    5. Clamp:
       multiplier = max(theta["budget"]["hard_floor"], min(1.0, multiplier))

    Design note: regime_factor reduces available budget BEFORE tier deductions.
    This reflects structural headwinds (R3 = deeper drawdowns, less rope).
    Tier deductions are additive penalties on top — they catch transitions.

  STALENESS PENALTY (applied before tier counting):
    If ANY signal is > theta["multiplier"]["staleness_days"] trading days old,
    add 1 phantom tier. This prevents overconfidence on stale macro data.

  FAIL-OPEN:
    If any signal is None/missing, treat as 0 tiers contributed by that signal
    (conservative — no penalty for unknown). Log warning with missing_signal key.
    If regime is None/unknown, treat as "R1" (no penalty — don't penalise
    for data gaps).

────────────────────────────────────────────────────────────────────────
PHASE 4: Circuit breakers & position stops
────────────────────────────────────────────────────────────────────────

check_circuit_breakers(current_drawdown_pct, budget_pct,
                       position_drawdowns, cross_sectional_corr, theta)
    → list[dict]
    Each dict: {"type": "hard_floor"|"systemic_event", "triggered": bool,
                "detail": str, "action": str}

  HARD FLOOR:
    triggered = current_drawdown_pct >= \
                theta["circuit_breakers"]["hard_floor_trigger"] * abs(budget_pct)
    Action: "Set multiplier to hard_floor, liquidate within 2 trading days."

  SYSTEMIC EVENT:
    n_breached = count(positions where drawdown < systemic_event["pct_threshold"])
    pct_breached = n_breached / total_positions
    triggered = pct_breached >= systemic_event["pct_positions"] \
                AND cross_sectional_corr > systemic_event["corr_threshold"]
    Action: "Systemic breakdown. Floor at hard_floor. Do not re-enter until
             hysteresis clears."

check_position_stops(position_drawdowns, asset_classes, theta)
    → list[dict]
    Each dict: {"ticker": str, "drawdown": float, "threshold": float,
                "asset_class": str, "triggered": bool, "action": str}

  For each position:
    asset_class = asset_classes.get(ticker, "unknown")
    threshold = theta["position_stops"][asset_class]
    triggered = position_drawdown <= threshold  # drawdowns are negative
    Action: f"Exit {ticker}, proceeds to BIL. No re-entry for
             {theta['hysteresis']['position_stop_reentry_days']} trading days."

check_reentry_hysteresis(last_breaker_time, last_stop_times, current_time, theta)
    → bool  # True = re-entry is BLOCKED

  Returns True if ANY of:
    - last_breaker_time is within theta["hysteresis"]["breaker_reentry_days"]
      trading days of current_time
    - Any last_stop_time is within theta["hysteresis"]["position_stop_reentry_days"]
      trading days of current_time

  Trading days counted as calendar days × 5/7 (rough).
  Design decision: use calendar days for simplicity (hysteresis is approximate).

────────────────────────────────────────────────────────────────────────
JUNIOR IMPLEMENTATION NOTES
────────────────────────────────────────────────────────────────────────

- All functions are PURE — no external API calls, no I/O. Inputs are
  pre-computed by the caller (scenario.py or a cron job).
- Phase 1: implement compute_exposure_multiplier().
  Phase 2: add compute_exposure_multiplier_v2(). Keep v1 as a fallback.
  Phase 4: implement breaker + stop + hysteresis functions.
- All thresholds come from theta dict. Never hardcode values.
- Fail-open everywhere: missing/None inputs → log warning, return safe default.
"""
