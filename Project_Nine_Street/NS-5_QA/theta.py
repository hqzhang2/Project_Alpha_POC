#!/usr/bin/env python3
"""
NS-5 Theta — owner parameter vector (v1 subset).

Single source of truth for concentration-axis defaults and weightings.
Overridable: load from a JSON file or import and mutate before wiring.
"""
from __future__ import annotations
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Default v1 Θ (concentration axis only — drift/tax/lev/deferred)
# ---------------------------------------------------------------------------

THETA_DEFAULTS = {
    # --- Risk tolerance (scales caps/bands; v1 uses as label only) ---
    "risk_tolerance": "moderate",       # conservative | moderate | aggressive
    "target_vol": 0.10,                 # annualized (unused in v1; drift v2)

    # --- Policy portfolio ---
    "policy_weights": {},               # {ticker: weight} — set per-portfolio
    "policy_name": "Unnamed Policy",

    # --- Concentration caps ---
    "max_single_name_pct": 0.10,        # single position max (10%)
    "max_sector_pct": 0.30,             # single sector max (30%)
    "effective_n_floor": 12,            # minimum effective-N

    # --- Factor grading ---
    "factor_tolerance_sigma": 2.0,      # ±2σ = flag boundary (C- grade)
    "factor_regression_window": 2,      # years for the OLS snapshot

    # --- Composite concentration grade weights ---
    "concentration_axis_weights": {
        "factor_loading": 0.40,
        "sector": 0.25,
        "effective_n": 0.20,
        "tail_correlation": 0.15,
    },

    # --- Grade thresholds (frontier-set, do not change) ---
    # deviation-in-sigma → letter : upper bound
    "sigma_grade_bounds": [
        (0.5,  "A"),
        (1.5,  "B"),
        (2.5,  "C"),
        (3.5,  "D"),
        (float("inf"), "F"),
    ],
    # composite letter from numeric score
    "letter_score_bounds": [
        (4.5, "A"),
        (3.5, "B"),
        (2.5, "C"),
        (1.5, "D"),
        (0.0, "F"),
    ],

    # --- Tail correlation ---
    "tail_pctile": 5,                   # worst N% of days
    "tail_corr_threshold": 0.7,         # pairwise corr > this → flag
    "top_n_for_tail": 5,                # check largest N positions

    # --- Sector mapper (static for v1) ---
    # Extended by portfolio's tickers at runtime; this is the fallback.
    "sector_map": {
        "SPY": "Equity-Large",
        "IVV": "Equity-Large",
        "QQQ": "Equity-Tech",
        "IWM": "Equity-Small",
        "TLT": "Fixed-Income-Long",
        "IEF": "Fixed-Income-Intermediate",
        "SHY": "Fixed-Income-Short",
        "BIL": "Cash",
        "GLD": "Commodity",
        "USO": "Commodity",
        "VTV": "Equity-Value",
        "VUG": "Equity-Growth",
        "MTUM": "Equity-Momentum",
        "XLK": "Sector-Tech",
        "XLF": "Sector-Financials",
        "XLV": "Sector-Healthcare",
        "XLY": "Sector-Consumer-Discretionary",
        "XLP": "Sector-Consumer-Staples",
        "XLE": "Sector-Energy",
        "XLI": "Sector-Industrials",
        "XLB": "Sector-Materials",
        "XLU": "Sector-Utilities",
        "XLRE": "Sector-Real-Estate",
        "XLC": "Sector-Communication-Services",
        # Common mega-cap / large-cap stocks (GICS sectors, static for v1)
        "AAPL": "Sector-Tech",
        "MSFT": "Sector-Tech",
        "NVDA": "Sector-Tech",
        "AVGO": "Sector-Tech",
        "CRM": "Sector-Tech",
        "ORCL": "Sector-Tech",
        "AMD": "Sector-Tech",
        "ADBE": "Sector-Tech",
        "QCOM": "Sector-Tech",
        "ACN": "Sector-Tech",
        "CSCO": "Sector-Tech",
        "IBM": "Sector-Tech",
        "INTU": "Sector-Tech",
        "MU": "Sector-Tech",
        "INTC": "Sector-Tech",
        "GOOGL": "Sector-Communication-Services",
        "GOOG": "Sector-Communication-Services",
        "META": "Sector-Communication-Services",
        "NFLX": "Sector-Communication-Services",
        "DIS": "Sector-Communication-Services",
        "VZ": "Sector-Communication-Services",
        "T": "Sector-Communication-Services",
        "AMZN": "Sector-Consumer-Discretionary",
        "TSLA": "Sector-Consumer-Discretionary",
        "HD": "Sector-Consumer-Discretionary",
        "MCD": "Sector-Consumer-Discretionary",
        "NKE": "Sector-Consumer-Discretionary",
        "COST": "Sector-Consumer-Staples",
        "PG": "Sector-Consumer-Staples",
        "WMT": "Sector-Consumer-Staples",
        "KO": "Sector-Consumer-Staples",
        "PEP": "Sector-Consumer-Staples",
        "JPM": "Sector-Financials",
        "BRK-B": "Sector-Financials",
        "V": "Sector-Financials",
        "MA": "Sector-Financials",
        "BAC": "Sector-Financials",
        "WFC": "Sector-Financials",
        "GS": "Sector-Financials",
        "UNH": "Sector-Healthcare",
        "LLY": "Sector-Healthcare",
        "JNJ": "Sector-Healthcare",
        "ABBV": "Sector-Healthcare",
        "MRK": "Sector-Healthcare",
        "PFE": "Sector-Healthcare",
        "TMO": "Sector-Healthcare",
        "XOM": "Sector-Energy",
        "CVX": "Sector-Energy",
        "COP": "Sector-Energy",
        "SLB": "Sector-Energy",
        "CAT": "Sector-Industrials",
        "GE": "Sector-Industrials",
        "BA": "Sector-Industrials",
        "HON": "Sector-Industrials",
        "LIN": "Sector-Materials",
        "NEE": "Sector-Utilities",
    },

    # --- Sector grading (frontier-set: worst-of rule, do not change) ---
    # grade on the WORST-deviating sector, not the average.
    "sector_ratio_bounds": [  # ratio = sector_weight / cap → letter
        (0.5, "A"),      # well within cap
        (1.0, "B"),      # ≤ cap = fine (not flagged)
        (1.25, "C"),     # moderate overage
        (1.5, "D"),      # significant overage
        (float("inf"), "F"),
    ],
    # benchmark sector weights (informational; empty = no benchmark comparison)
    "benchmark_sector_weights": {},

    # --- Tail-correlation grading (frontier-set) ---
    "tail_corr_grade": [                # flagged-pair count → letter
        (0, "A"),
        (1, "B"),
        (2, "C"),
    ],

    # ==================================================================
    # Drift axis (v2) — time-series consumer of the same 5-factor model
    # ==================================================================

    # --- Weight drift ---
    "drift_band": 0.20,                 # ±20% relative: |w − target| / target > band → flag

    # --- Risk drift ---
    "risk_budget": {
        "target_vol": 0.14,             # annualized σ* (policy risk budget)
        "var_95_limit": -0.15,          # daily VaR(95%) limit (%)
        "cvar_95_limit": -0.22,         # daily CVaR(95%) limit (%)
        "vol_spike_sigma": 1.5,         # trailing vol > long-run avg × Nσ → flag
    },

    # --- Style/factor drift ---
    "style_tolerance": {
        "factor_sigma": 1.5,            # |β_i − β*_i| / se_i > this → flagged
        "qqq_corr_threshold": 0.90,     # corr to QQQ > this → "this IS QQQ" flag
    },

    # --- Frontier drift ---
    "frontier_thresholds": {
        "sharpe_degradation": 0.15,     # long-run Sharpe − trailing Sharpe > this → flag
        "tangency_shift": 0.15,         # max weight diff in tangency mix → flag
        "bond_corr_sign_flip": True,    # stock-bond sign flip → independent flag
    },

    # --- Composite drift grade weights ---
    "drift_axis_weights": {
        "weight_drift": 0.15,
        "risk_drift": 0.25,
        "style_drift": 0.30,
        "frontier_drift": 0.30,
    },

    # --- Drift severity → score (lower = worse; descending thresholds) ---
    "drift_severity_bounds": [
        (5.0, "green"),     # clean — no action
        (3.5, "yellow"),    # moderate — monitor
        (2.0, "orange"),    # elevated — action needed
        (0,   "red"),       # critical — re-plan trigger
    ],

    # --- Tax axis (v3) — None = disabled; set to TAX_DEFAULTS to activate ---
    "tax": None,
}

# =============================================================================
# TAX_DEFAULTS — the recommended tax profile for axis activation.
# Pass `tax=theta.TAX_DEFAULTS` to load_theta() to enable the tax axis.
# US personal income regime only (federal + optional state).
# =============================================================================

TAX_DEFAULTS = {
    # Tax rates (single source of truth — drags computed, not stored twice)
    "federal_bracket": 0.37,            # top marginal ordinary rate
    "ltcg_rate": 0.20,                   # federal long-term capital gains rate
    "niit": True,                        # 3.8% net investment income tax
    "state_rate": 0.0,                   # state income rate (0 = none, e.g. FL/TX)

    # Drag rates (computed via _compute_drags — never hardcoded elsewhere)
    "ordinary_drag": 0.408,             # 0.37 + 0.038 + 0.0
    "ltcg_drag": 0.238,                  # 0.20 + 0.038 + 0.0
    "blended_1256_drag": 0.28,           # 0.60*ltcg_drag + 0.40*ordinary_drag
    "roc_drag": 0,                       # deferred to sale; 0 current

    # Basis erosion thresholds (ROC position locked-in warning)
    "erosion_thresholds": [0.50, 0.75, 0.90],

    # Wash-sale window (days before/after a loss sale)
    "wash_sale_window_days": 30,

    # Per-account-type tax treatment
    "account_treatment": {
        "taxable":  {"dividend_drag": True,  "sale_taxable": True,  "tlh_available": True,  "withdrawal_always_ordinary": False},
        "ira":      {"dividend_drag": False, "sale_taxable": False, "tlh_available": False, "withdrawal_always_ordinary": True},
        "401k":     {"dividend_drag": False, "sale_taxable": False, "tlh_available": False, "withdrawal_always_ordinary": True},
        "roth":     {"dividend_drag": False, "sale_taxable": False, "tlh_available": False, "withdrawal_always_ordinary": False},
    },

    # Distribution character classifier (per-ticker, PM-maintained)
    # Values: "qualified" | "ordinary" | "roc" | "sec1256"
    # ROC tickers require annual_roc_rate (e.g. CAIE: 0.14 = 14%/yr)
    # Unknown tickers default to "qualified" (conservative — understates drag)
    "distribution_character": {
        "CAIE": {"character": "roc",      "annual_roc_rate": 0.14},
        "JEPQ": {"character": "ordinary"},
        "SPYI": {"character": "sec1256"},
        "VOO":  {"character": "qualified"},
        "SCHD": {"character": "qualified"},
        "TLT":  {"character": "ordinary"},
    },

    # Tax severity -> color
    "tax_severity_bounds": [
        (5.0, "green"),
        (3.5, "yellow"),
        (2.0, "orange"),
        (0,   "red"),
    ],
}



def load_theta(path: str = None, **overrides) -> dict:
    """Load Θ from a JSON file or return defaults, with runtime overrides."""
    import json, copy
    theta = copy.deepcopy(THETA_DEFAULTS)
    if path:
        with open(path) as fh:
            theta.update(json.load(fh))
    theta.update(overrides)
    return theta