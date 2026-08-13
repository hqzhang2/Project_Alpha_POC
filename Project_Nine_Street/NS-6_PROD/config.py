"""
config.py — NS-6 Θ parameters (single source of truth).

All caps, bands, thresholds, weights live here. No hardcoded values in any
other module. Follows NS-5 theta.py pattern: THETA_DEFAULTS + load_theta().

Frontier-owned. Junior: read from this dict, never override. Tests monkeypatch
specific keys for edge cases but never change defaults.
"""

from copy import deepcopy

# ── unified grade→letter bounds (shared across NS-6 severity displays) ──
LETTER_SCORE_BOUNDS = [(4.5, "A"), (3.5, "B"), (2.5, "C"), (1.5, "D"), (0.0, "F")]
# descending — same pattern as NS-5

SEVERITY_BOUNDS = [(5.0, "green"), (3.5, "yellow"), (2.0, "orange"), (0.0, "red")]
# descending — ascending returns "red" for everything (NS-5 pitfall)

THETA_DEFAULTS = {
    # ═══════════════════════════════════════════════════════════════════
    # BUDGET — drawdown budget definition
    # ═══════════════════════════════════════════════════════════════════
    "budget": {
        # Absolute floor on drawdown budget (basis points).
        # If SPY is only down 2%, your budget is still 5% — you always
        # have SOME room before the floor. Below this, everything is
        # emergency. 500 bps = 5%.
        "absolute_floor_bps": 500,

        # What fraction of SPY's drawdown is YOUR drawdown budget.
        # 0.50 = "half the drawdown of SPY."
        "spy_dd_ratio": 0.50,

        # Hard floor exposure multiplier — the lowest equity exposure
        # the system will ever allow. 0.25 = 25% equity, 75% BIL.
        "hard_floor": 0.25,

        # Soft warning threshold — budget consumed above this triggers
        # advisory alerts but no forced action.
        "soft_warning_pct": 0.30,
    },

    # ═══════════════════════════════════════════════════════════════════
    # EXPOSURE MULTIPLIER — how budget remaining maps to equity exposure
    # ═══════════════════════════════════════════════════════════════════
    "multiplier": {
        # Phase 1: linear budget→multiplier mapping.
        # multiplier = budget_remaining_pct (clamped to [hard_floor, 1.0])
        # 100% budget → 1.0, 50% → 0.5, 0% → hard_floor

        # Phase 2+: tier reduction per active signal.
        # Each active tier subtracts this from the effective multiplier
        # BEFORE the hard_floor clamp.
        "tier_deduction": 0.15,

        # Maximum number of tiers that can be active simultaneously.
        # Currently 3: vol_regime, corr_sign, vix_regime.
        "max_tiers": 3,

        # Regime budget factors — multiplies the available budget before
        # tier deductions. These reflect the structural headwind/tailwind
        # of the macro environment on drawdown probability.
        "regime_budget_factors": {
            "R1": 1.00,   # Expansion — full budget available
            "R2": 0.75,   # Overheating — inflation pressure
            "R3": 0.50,   # Recession — drawdowns are deeper
            "R4": 0.25,   # Stagflation — cash preservation
        },

        # Signal thresholds for tier activation.
        "signal_thresholds": {
            # NS-5 drift axis: trailing_vol / long_run_vol
            "vol_ratio": 1.5,      # > 1.5 → Tier 1 active (risk expanding)

            # NS-5 regime axis: stock-bond correlation sign
            "corr_sign_positive": True,  # True = positive corr → Tier 2 active

            # A_T sentiment: VIX level + trend
            "vix_level": 28.0,     # VIX > 28 → candidate
            "vix_trend_positive": True,  # AND rising → Tier 3 active
        },

        # Staleness penalty: if any signal is >N trading days stale,
        # add one phantom tier (same deduction as a real tier).
        # This prevents the system from being overconfident on stale data.
        "staleness_days": 2,
    },

    # ═══════════════════════════════════════════════════════════════════
    # FAST DE-RISKING (v2) — daily VIX-smile exposure cap
    # ═══════════════════════════════════════════════════════════════════
    # Replaces the SLOW quarterly budget-multiplier as the PRIMARY de-risking
    # mechanism. Evidence (fast_derisk_experiment.py, 2017-2026): the VIX
    # smile applied daily (1-day lag, no lookahead) preserves the growth
    # factor's return (Sharpe 0.96-0.98) while the quarterly budget multiplier
    # destroys it (Sharpe 0.70-0.81). The binary crisis off-switch is DROPPED —
    # it reinvents NS-1's "confirmation gates too slow" failure (eat the crash,
    # miss the V-recovery). A floored crisis keeps a minimum equity stake.
    "fast_derisk": {
        # VIX smile curve — exposure CAP by VIX level (NS-1 v3, validated).
        # Ordered ascending by VIX. vix < level → cap. Last entry is the
        # ceiling for VIX above the final level.
        "vix_smile": [
            [12.0, 0.95], [15.0, 1.00], [20.0, 0.90], [25.0, 0.80],
            [30.0, 0.65], [35.0, 0.50], [40.0, 0.35], [50.0, 0.55],
            [60.0, 0.70], [100.0, 0.85],
        ],
        # Crisis hysteresis: enter crisis mode when VIX >= in, exit when
        # VIX <= out (stays unchanged between → no flicker).
        "crisis_in": 28.0,
        "crisis_out": 23.0,
        # Minimum equity exposure during crisis mode — NEVER zero.
        # 0.30 = keep 30% equity. Avoids "miss the V-recovery" (evidence:
        # +30% floor = Sharpe 0.98 vs 0.84 at hard zero).
        "crisis_floor": 0.30,
        # Decide exposure using VIX close N days prior (no lookahead).
        "lookback_lag": 1,
        # Default cap when VIX is unavailable (fail-open, mid-smile).
        "default_cap": 0.65,
    },

    # ═══════════════════════════════════════════════════════════════════
    # CIRCUIT BREAKERS — non-negotiable hard floors
    # ═══════════════════════════════════════════════════════════════════
    "circuit_breakers": {
        # Hard floor trigger: current_drawdown ≥ this fraction of budget.
        # 0.90 = "you've consumed 90% of your budget — survival mode."
        "hard_floor_trigger": 0.90,

        # Systemic event: N% of positions simultaneously down >X%.
        "systemic_event": {
            "pct_positions": 0.60,     # ≥60% of positions
            "pct_threshold": -0.15,    # down >15%
            "corr_threshold": 0.70,    # AND cross-sectional corr > 0.7
        },
    },

    # ═══════════════════════════════════════════════════════════════════
    # POSITION STOPS — per-position drawdown thresholds by asset class
    # ═══════════════════════════════════════════════════════════════════
    "position_stops": {
        "equity": -0.25,       # individual stock down 25% from entry → exit
        "bond_etf": -0.15,     # bond ETF down 15% → exit
        "commodity_etf": -0.20,  # commodity ETF down 20% → exit
        "cash_proxy": -0.05,   # cash proxy (BIL) down 5% → exit
        "unknown": -0.20,      # fallback for unclassified tickers
    },

    # ═══════════════════════════════════════════════════════════════════
    # RE-ENTRY HYSTERESIS — wait periods after forced actions
    # ═══════════════════════════════════════════════════════════════════
    "hysteresis": {
        # After any circuit breaker fires: no re-entry for N trading days.
        "breaker_reentry_days": 5,

        # After a position stop: no re-entry in same ticker for N days.
        "position_stop_reentry_days": 20,

        # After a breaker, drift-based re-entry requires consecutive days
        # of stable drift alerts (no new alerts fired).
        "drift_stable_days": 5,
    },

    # ═══════════════════════════════════════════════════════════════════
    # REBALANCING — funding path generation parameters
    # ═══════════════════════════════════════════════════════════════════
    "rebalancing": {
        # Minimum trade size as fraction of NAV. Trades below this are
        # suppressed to avoid noise adjustments.
        "min_trade_size_pct": 0.005,  # 0.5% of NAV

        # Relative rebalancing band. Don't trim a position whose current
        # weight is within ±20% of its target. Target=5%, current ∈ [4%,6%]
        # → no trade. Daryanani (2007) standard.
        "band_rel": 0.20,

        # Minimum BIL reserve as fraction of NAV to qualify for
        # "fund from cash reserve" path.
        "cash_reserve_min_pct": 0.02,

        # Number of funding paths to generate.
        "max_paths": 5,

        # Path ranking order (default). PM can override.
        "ranking_order": ["fewest_trades", "lowest_tax", "best_risk"],
    },

    # ═══════════════════════════════════════════════════════════════════
    # COVERED CALLS — income generation gate  (Phase 3)
    # ═══════════════════════════════════════════════════════════════════
    "covered_calls": {
        # Minimum exposure multiplier to allow ANY covered call writing.
        # Below this, don't cap upside on positions you're uncertain about.
        "gate_multiplier": 0.60,

        # Overwrite percentage by multiplier band.
        "overwrite_pct": {
            "full": 0.50,     # multiplier ≥ 0.80 → 50% of position notional
            "reduced": 0.25,  # multiplier ∈ [0.60, 0.80) → 25%
            "none": 0.00,     # multiplier < 0.60 → no calls
        },
        "full_threshold": 0.80,

        # Call specifications.
        "dte_min": 30,
        "dte_max": 45,
        "delta_target": 0.25,  # 0.20-0.30 delta range

        # Management rules.
        "roll_dte": 21,       # roll/close at 21 DTE
        "profit_close_pct": 0.50,  # close at 50% of max profit

        # Option liquidity gate: bid-ask spread must be < this fraction
        # of mid-price for the option to be considered liquid.
        "min_liquidity_spread": 0.05,
    },

    # ═══════════════════════════════════════════════════════════════════
    # PROTECTIVE PUTS — drawdown insurance overlay  (Phase 2)
    # ═══════════════════════════════════════════════════════════════════
    "protective_puts": {
        # Exposure multiplier threshold: below this, evaluate put overlay.
        "gate_multiplier": 0.80,

        # Put type by multiplier band.
        # multiplier ∈ [0.60, 0.80) → 5-10% OTM puts on SPY
        # multiplier ∈ [0.40, 0.60) → ATM puts on SPY
        # multiplier ∈ [hard_floor, 0.40) → ITM puts + individual puts
        "bands": {
            "otm": {"low": 0.60, "high": 0.80, "strike_pct": 0.05},  # 5% OTM
            "atm": {"low": 0.40, "high": 0.60, "strike_pct": 0.00},  # ATM
            "itm": {"low": None, "high": 0.40, "strike_pct": -0.05},  # 5% ITM
        },

        # Put specifications.
        "dte_min": 30,
        "dte_max": 45,

        # Cost comparison: put_cost vs sell_cost.
        # If put premium annualized is less than this fraction of the
        # position notional, prefer puts over selling.
        "annualized_cost_threshold": 0.06,  # 6% annualized = 0.5% per month

        # SPY put overlay covers this fraction of total equity notional.
        "spy_overlay_coverage": 0.50,
    },

    # ═══════════════════════════════════════════════════════════════════
    # IRON CONDOR — income during low-exposure periods  (Phase 5)
    # ═══════════════════════════════════════════════════════════════════
    "iron_condor": {
        # Only deploy when exposure multiplier is below this threshold
        # (portfolio is in cash-heavy mode — generate income on idle
        # capital).
        "max_multiplier": 0.50,

        # Condor specs.
        "dte_min": 30,
        "dte_max": 45,
        "short_delta": 0.16,   # ~16 delta short legs
        "wing_width_pct": 0.05,  # 5% wide wings
        "max_capital_pct": 0.10,  # max 10% of NAV in condor margin

        # Management.
        "roll_dte": 21,
        "profit_close_pct": 0.50,
    },

    # ═══════════════════════════════════════════════════════════════════
    # DRIFT ALERTS — quarterly + event-driven drift classification
    # ═══════════════════════════════════════════════════════════════════
    "drift_alert": {
        # Relative band for quarterly alert: position weight differs from
        # target by more than this fraction.
        "band_rel_warning": 0.20,  # ±20% of target → alert

        # Event-driven trigger: position exceeds this band immediately
        # regardless of schedule.
        "band_rel_urgent": 0.50,   # ±50% of target → urgent alert

        # NS-2 regime gating for overweight positions:
        # TRENDING → "MONITOR. Don't trim while trending."
        # MEAN_REV → "Consider trim. NS-2 neutral."
        # CRISIS → "Reduce. Regime hostile."
        # NO-EDGE (gated) → treated as MEAN_REV (conservative)
        "ns2_regime_override": {
            "TRENDING":   "MONITOR",
            "MEAN_REV":   "CONSIDER",
            "CRISIS":     "REDUCE",
            "NO-EDGE":    "CONSIDER",  # fallback — no edge = neutral
        },
    },

    # ═══════════════════════════════════════════════════════════════════
    # SCENARIO ENGINE — add/remove/replace analysis parameters
    # ═══════════════════════════════════════════════════════════════════
    "scenario": {
        # Price sensitivity bands: percentage offsets from current price
        # to project risk impact.
        "price_sensitivity_bands": [-0.05, 0.00, 0.05, 0.10],

        # Maximum funding paths to generate per scenario.
        "max_funding_paths": 5,

        # Minimum screener agreement for a ticker to be considered
        # a candidate for addition (overrideable by PM).
        "min_screener_agreement": 2,
    },
}


def load_theta(overrides=None):
    """Return a deep copy of defaults, optionally merged with overrides.

    Override dict can be a partial dict — only supplied keys are replaced.
    Used for per-request customisation (e.g. tighter budget in QA tests).
    """
    theta = deepcopy(THETA_DEFAULTS)
    if overrides:
        _deep_merge(theta, overrides)
    return theta


def _deep_merge(base, overrides):
    """Recursively merge overrides into base (mutates base)."""
    for k, v in overrides.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


# ═══════════════════════════════════════════════════════════════════════
# PROFILES — three switchable target points on the return/drawdown frontier
# ═══════════════════════════════════════════════════════════════════════
# The PM SWITCHES among these — not one locked target. Each profile is a
# complete bundle: what to hold (selection), how much defensive sleeve, and
# how aggressive de-risking is. The system computes all three points; the PM
# decides which one to BE at, and flips on regime shift / conviction change.
#
# Evidence-backed (2017-2026, from the NS-6 experiment series):
#   GROWTH              — MAG7 basket, Sharpe 1.33, DD ~-42%  (return-max)
#   BALANCED            — growth + sized sleeve, ~Sharpe 1.0-1.1, DD ~-25%
#   CAPITAL_PRESERVATION— GMV weighting, 0.22x DD, Sharpe 0.73  (drawdown-min)
#
# Each profile overrides theta keys + carries a selection/weighting hint.
# theta_overrides uses the same partial-deep-merge shape as load_theta().
PROFILES = {
    "growth": {
        "label": "GROWTH (return-max)",
        "description": "MAG7 growth basket, full de-risking floor, minimal sleeve. "
                       "Beats SPY on return AND Sharpe; accepts deep drawdowns.",
        "selection": "growth_basket",       # MAG7 growth names
        "weighting": "growth_90_10",        # 90% equity / 10% sleeve (return-max)
        "theta_overrides": {
            "budget": {"spy_dd_ratio": 1.00, "hard_floor": 0.50},
            "fast_derisk": {"crisis_floor": 0.30},
        },
    },
    "balanced": {
        "label": "BALANCED (growth + defensive sleeve)",
        "description": "Growth basket blended with a non-equity sleeve. Sits "
                       "mid-frontier: most of the growth return, much of the "
                       "drawdown protection.",
        "selection": "growth_basket",
        "weighting": "growth_sleeve_60_40",  # 60% growth / 40% defensive sleeve
        "theta_overrides": {
            "budget": {"spy_dd_ratio": 0.75, "hard_floor": 0.30},
            "fast_derisk": {"crisis_floor": 0.20},
        },
    },
    "capital_preservation": {
        "label": "CAPITAL PRESERVATION (drawdown-min)",
        "description": "GMV (minimum-variance) weighting + aggressive de-risking. "
                       "Halves SPY drawdown; trails SPY on return. The survival "
                       "mandate.",
        "selection": "value_screener",       # value/quality, defensive tilt
        "weighting": "gmv",                  # NS-5 global minimum variance
        "theta_overrides": {
            "budget": {"spy_dd_ratio": 0.50, "hard_floor": 0.25},
            "fast_derisk": {"crisis_floor": 0.20},
        },
    },
}


def load_profile(name):
    """Return (theta, selection, weighting) for a named profile.

    Raises KeyError for unknown names. theta is a deep-merged copy of
    THETA_DEFAULTS with the profile's overrides applied.
    """
    if name not in PROFILES:
        raise KeyError(f"unknown profile '{name}' (valid: {sorted(PROFILES)})")
    p = PROFILES[name]
    theta = load_theta(p.get("theta_overrides"))
    return theta, p["selection"], p["weighting"]


# ═══════════════════════════════════════════════════════════════════════
# REGIME-GATED SWITCH SUGGESTION (T4) — advisory, never auto
# ═══════════════════════════════════════════════════════════════════════
# Maps the NS-5 macro regime axis (R1-R4, GDP × CPI 2×2) to a SUGGESTED
# target profile. This is ADVISORY ONLY: the system computes what the regime
# implies; the PM decides whether/when to switch. Never auto-switch.
#
# Rationale (frontier decision, 2017-2026 regime distribution R1=17/R2=19/
# R3=2/R4=0):
#   R1 Expansion (growth↑ × inflation↓) → growth              — max return, no inflation headwind
#   R2 Overheating (growth↑ × inflation↑) → balanced          — still growth, but inflation pressure → sleeve
#   R3 Recession (growth↓ × inflation↓)   → capital_preservation — contraction, defensive
#   R4 Stagflation (growth↓ × inflation↑) → capital_preservation — worst quadrant, most defensive
#
# R3/R4 both map to capital_preservation (survival); the difference is
# confidence in the regime read, not the action.
REGIME_TO_PROFILE = {
    "R1": "growth",
    "R2": "balanced",
    "R3": "capital_preservation",
    "R4": "capital_preservation",
}

# A suggestion is only emitted when the regime row is fresher than this many
# days. Stale regime data → no suggestion (don't nudge on old macro reads).
REGIME_MAX_AGE_DAYS = 45


def suggest_profile(regime):
    """Advisory profile for a regime code. None if regime unknown.

    Pure mapping — no I/O, no side effects. The caller (qa_server) decides
    whether to surface it based on freshness/active-vs-suggested.
    """
    return REGIME_TO_PROFILE.get(regime)


# ═══════════════════════════════════════════════════════════════════════
# MODEL PORTFOLIOS — per-profile default compositions (fallback)
# ═══════════════════════════════════════════════════════════════════════
# Used when NO NS-5 portfolio is selected in the cockpit (portfolio_source
# is "model"). Weights sum to 1.0. Derived from the validated experiments.
# Each is the "model portfolio name" the dashboard shows per profile.
MODEL_PORTFOLIOS = {
    "growth": {
        # MAG7 equal-weight growth basket (the return engine, Sharpe 1.33).
        "AAPL": 0.143, "MSFT": 0.143, "NVDA": 0.143, "GOOGL": 0.143,
        "AMZN": 0.143, "META": 0.143, "TSLA": 0.142,
    },
    "balanced": {
        # 60% growth basket / 40% defensive sleeve (sweet spot, Sharpe 1.22).
        "AAPL": 0.086, "MSFT": 0.086, "NVDA": 0.086, "GOOGL": 0.086,
        "AMZN": 0.086, "META": 0.086, "TSLA": 0.084,
        "TLT": 0.20, "GLD": 0.10, "IEF": 0.05, "BIL": 0.04, "DBC": 0.01,
    },
    "capital_preservation": {
        # GMV tilt → defensive sleeve + cash (drawdown-min, 0.22x DD).
        "TLT": 0.30, "GLD": 0.20, "IEF": 0.15, "BIL": 0.25, "DBC": 0.05,
        "SPY": 0.05,  # minimal equity for liquidity/participation
    },
}


# ── Portfolio → policy pairing (drift target, PM decision 2026-08-13) ───
# The drift check's TARGET = the selected portfolio's policy (option 2).
# NS-5 keeps portfolios and policies as separate stores with no linkage,
# so the pairing is an explicit PM-controlled map here. A portfolio not
# listed (or a policy name missing from NS-5's store) falls back to
# DEFAULT_WEIGHTS in qa_server._drift_target().
PORTFOLIO_POLICIES = {
    "Hyperscaler": "60/40 SPY/TLT",
}


def model_portfolio(profile):
    """Return the model portfolio weights dict for a profile, or {} if unknown."""
    return dict(MODEL_PORTFOLIOS.get(profile, {}))
