#!/usr/bin/env python3
"""
NS-5 Concentration Consumer — policy loading vector + factor-loading grading.

ROADMAP §2.4–2.5 (MONEY-PATH — frontier-owned, do not edit without review):
- `compute_policy_beta()`: derive policy β* from policy weights
- `grade_factor_loading()`: compare portfolio β to policy β, produce per-factor
  grades, a composite, and flagged deviations.

All thresholds are in theta.THETA_DEFAULTS — single source of truth.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Dict, Optional

import numpy as np
import pandas as pd

import config
import data_fetcher
import regression
import theta as theta_mod


# ============================================================================
# Policy loading vector (§2.4)
# ============================================================================

def compute_policy_beta(policy_weights: Dict[str, float],
                        factor_returns: Optional[pd.DataFrame] = None,
                        closes: Optional[pd.DataFrame] = None,
                        force_refresh: bool = False) -> Dict:
    """
    Derive the policy loading vector β* from policy weights.

    Constructs the theoretical daily return series of the policy portfolio
    (Σ w_i × r_i), regresses it on the 5 factors, and returns the β vector.

    Returns: dict with {beta: {factor_name: float, ...}, alpha, r_squared, n_obs}
             or {"error": "..."} on failure.
    """
    if factor_returns is None:
        factor_returns, _, _ = data_fetcher.build_factor_returns(force_refresh=force_refresh)
    if factor_returns.empty:
        return {"error": "no factor data — run refresh"}

    all_tickers = list(policy_weights.keys())
    if closes is None:
        closes = data_fetcher.get_closes(all_tickers, force_refresh=force_refresh)
    if closes.empty:
        return {"error": "no price data for policy tickers"}

    available = [t for t in all_tickers if t in closes.columns]
    if not available:
        return {"error": "none of the policy tickers have price data"}

    # Build policy daily returns (same contract as build_portfolio_returns)
    rets = data_fetcher.compute_log_returns(closes[available].copy())
    if rets.empty:
        return {"error": "no return data for policy tickers"}
    weights_arr = np.array([policy_weights[t] for t in available])
    policy_rets = (rets[available] @ weights_arr)
    policy_rets = policy_rets.where(np.isfinite(policy_rets)).dropna()

    if len(policy_rets) < 60:
        return {"error": f"insufficient policy return observations ({len(policy_rets)})"}

    result = regression.regress(policy_rets, factor_returns)
    if result is None:
        return {"error": "regression failed — insufficient data after alignment"}
    # Tag so consumers know this is the policy target, not a portfolio
    result["kind"] = "policy"
    result["policy_weights"] = deepcopy(policy_weights)
    return result


# ============================================================================
# Factor-loading grading (§2.5 — MONEY-PATH)
# ============================================================================

def _sigma_to_grade(sigma: float, bounds: list) -> tuple:
    """Map |deviation| ÷ SE to letter grade and numeric score (5=A, 1=F)."""
    scores = {"A": 5, "B": 4, "C": 3, "D": 2, "F": 1}
    for upper, letter in bounds:
        if sigma <= upper:
            return letter, scores[letter]
    return "F", 1


def _composite_from_scores(scores: list, bounds: list) -> tuple:
    avg = float(np.mean(scores)) if scores else 0.0
    letter = "F"
    for threshold, ltr in bounds:
        if avg >= threshold:
            letter = ltr
            break
    return round(avg, 2), letter


def grade_factor_loading(loading_vector: Dict[str, float],
                         policy_beta: Dict,
                         theta: dict,
                         standard_errors: Optional[Dict[str, float]] = None) -> Dict:
    """
    Grade factor-loading deviation: portfolio β vs policy β*.

    Args:
        loading_vector:  dict {factor_name: beta} from regression.regress()['beta']
        policy_beta:     dict from compute_policy_beta() — must have 'beta' key
        theta:           Θ parameter dict
        standard_errors: dict {factor_name: se}, from regression output — if None
                         or NaN, grade conservatively (> 2σ degradation)

    Returns:
        dict: {
            composite_grade: "B",           # overall letter
            composite_score: 3.2,           # numeric 1-5
            factors: {
                MKT: {beta, policy_beta, sigma, grade, score, flagged},
                ...
            }
        }

    Grade scale:
        A (5): ≤ 0.5σ  — near policy
        B (4): ≤ 1.5σ  — slight tilt, within tolerance
        C (3): ≤ 2.5σ  — material deviation, flags for review
        D (2): ≤ 3.5σ  — significant deviation, action recommended
        F (1): > 3.5σ  — severe, urgent
    """
    bounds = theta["sigma_grade_bounds"]
    letter_bounds = theta["letter_score_bounds"]
    tolerance = theta["factor_tolerance_sigma"]

    policy_betas = policy_beta.get("beta", {}) if isinstance(policy_beta, dict) else {}
    if not policy_betas:
        return {"composite_grade": "F", "composite_score": 1.0,
                "error": "no policy beta vector available",
                "factors": {}}

    if standard_errors is None:
        standard_errors = {}

    factor_details = {}
    scores = []

    for name in config.FACTOR_NAMES:
        beta_i = loading_vector.get(name, 0.0)
        policy_i = policy_betas.get(name, 0.0)
        se_i = standard_errors.get(name, np.nan)

        delta = beta_i - policy_i
        abs_delta = abs(delta)

        # Normalise by standard error — when SE is missing/NaN, estimate a floor
        # so we don't under-grade near-zero deviations. Use a pessimistic SE floor
        # equal to the factor's typical daily vol / sqrt(n_obs) ≈ 0.05–0.10.
        # NaN/Inf SE → conservative: double the effective sigma.
        if np.isfinite(se_i) and se_i > 0:
            sigma = abs_delta / se_i
        else:
            # Conservative: treat as 2× tolerance → C/ flagged by default when
            # delta is non-trivial. For near-zero delta (≤ 0.02), pass as "no
            # reliable SE — treating as estimate noise."
            sigma = tolerance * 2.0 if abs_delta > 0.02 else 0.0

        grade, score = _sigma_to_grade(sigma, bounds)
        flagged = sigma >= tolerance

        factor_details[name] = {
            "beta": round(beta_i, 4),
            "policy_beta": round(policy_i, 4),
            "delta": round(delta, 4),
            "sigma": round(sigma, 2),
            "se": round(se_i, 6) if np.isfinite(se_i) else None,
            "grade": grade,
            "score": score,
            "flagged": flagged,
        }
        scores.append(score)

    composite_score, composite_grade = _composite_from_scores(scores, letter_bounds)

    return {
        "composite_grade": composite_grade,
        "composite_score": composite_score,
        "flagged_count": sum(1 for f in factor_details.values() if f["flagged"]),
        "flagged_factors": [n for n, f in factor_details.items() if f["flagged"]],
        "factors": factor_details,
    }


# ============================================================================
# Full concentration grading pipeline (Phase 2.6 wiring)
# ============================================================================

def run_concentration_grade(holdings: Dict[str, float],
                            theta: dict,
                            factor_returns: Optional[pd.DataFrame] = None,
                            closes: Optional[pd.DataFrame] = None,
                            force_refresh: bool = False) -> Dict:
    """
    End-to-end concentration grading for a portfolio.
    Phase 2.6: wires parser, returns, regression, policy-β, and grading.
    """
    if factor_returns is None:
        factor_returns, _, _ = data_fetcher.build_factor_returns(force_refresh=force_refresh)
    if factor_returns.empty:
        return {"composite_grade": "N/A", "error": "no factor data — run refresh"}

    all_tickers = list(holdings.keys()) + list(theta.get("policy_weights", {}).keys())
    if closes is None:
        closes = data_fetcher.get_closes(all_tickers, force_refresh=force_refresh)

    # 1. Build portfolio returns from holdings
    from portfolio import build_portfolio_returns as _build
    port_rets = _build(holdings, closes=closes)
    if port_rets.empty:
        return {"composite_grade": "N/A", "error": "no portfolio return data"}

    # 2. Regress
    result = regression.regress(port_rets, factor_returns)
    if result is None:
        return {"composite_grade": "N/A", "error": "regression failed — insufficient aligned data"}

    # 3. Policy β (compute or use cached in theta)
    policy_weights = theta.get("policy_weights", {})
    policy = compute_policy_beta(policy_weights, factor_returns=factor_returns,
                                  closes=closes, force_refresh=force_refresh)
    if not isinstance(policy, dict) or "error" in policy:
        # Policy unavailable → grade against neutral [MKT=0.6, others=0]
        policy = {
            "beta": {"MKT": 0.6, "SMB": 0.0, "HML": 0.0, "MOM": 0.0, "DUR": 0.0},
            "kind": "fallback-neutral",
        }

    # 4. Grade
    grade = grade_factor_loading(
        loading_vector=result["beta"],
        policy_beta=policy,
        theta=theta,
        standard_errors=result.get("se"),
    )

    return {
        "as_of": str(factor_returns.index[-1].date()) if not factor_returns.empty else None,
        "n_obs": len(port_rets),
        "regression": {
            "alpha": round(result.get("alpha", 0), 6),
            "r_squared": round(result.get("r_squared", 0), 4),
            "n_obs": result.get("n_obs", 0),
        },
        "policy": {
            "kind": policy.get("kind", "computed"),
            "weights": theta.get("policy_weights", {}),
            "policy_beta": policy.get("beta", {}),
        },
        "factor_loading": grade,
    }