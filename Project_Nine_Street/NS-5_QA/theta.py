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
    },
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