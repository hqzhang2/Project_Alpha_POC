#!/usr/bin/env python3
"""
NS-5 Regime Axis — grade functions, composite merger, and tweak language.
Money-path: methodology, thresholds, and language (frontier-owned).
Checker functions (deterministic math) are cheap-model work (Phase 2b).

Four sub-axes per research doc §5.1:
  1. Frontier shift — Euclidean distance: all-regime GMV → current-regime GMV
  2. Tangency degradation — current-regime tangency Sharpe / all-regime Sharpe
  3. Policy-regime gap — distance from policy weights to current-regime frontier
  4. Correlation structure — stock-bond corr matches regime expectation?

Enhancer rule (§5.3): regime acts as a multiplier on the other axes' composite
score — it adjusts for the macro environment but never penalises below the
average of the other three axes (Hong, 2026-08-09).

Module: Project_Nine_Street/NS-5_QA/regime.py
Author: Frontier LLM (deepseek-v4-pro), 2026-08-09
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

import theta as theta_mod


# =============================================================================
# Helpers — shared with drift/tax (same contract)
# =============================================================================

def _score_to_letter(score: float, bounds: list) -> str:
    """Map score to letter grade via descending thresholds."""
    for threshold, letter in bounds:
        if score >= threshold:
            return letter
    return "F"


def _severity(score: float, theta: dict) -> str:
    """Score → severity (green/yellow/orange/red) via regime_severity_bounds.

    Severity bounds MUST be descending or everything reads 'red'
    (ascending-first-match pitfall — same as drift/tax axes).
    """
    bounds = theta.get(
        "regime_severity_bounds",
        [(5.0, "green"), (3.5, "yellow"), (2.0, "orange"), (0, "red")],
    )
    for threshold, sev in bounds:
        if score >= threshold:
            return sev
    return bounds[-1][1] if bounds else "green"


def _euclidean_distance(w1: Dict[str, float], w2: Dict[str, float]) -> float:
    """Euclidean distance between two weight vectors (same ticker universe)."""
    keys = set(w1) | set(w2)
    if not keys:
        return 0.0
    total = sum((w1.get(k, 0.0) - w2.get(k, 0.0)) ** 2 for k in keys)
    return math.sqrt(total)


# =============================================================================
# F2 — Per-sub-axis grade functions (frontier-owned: score → letter + severity)
# =============================================================================

def grade_frontier_shift(
    gmv_all: Dict[str, float],        # {ticker: weight} — long-run GMV
    gmv_current: Dict[str, float],    # {ticker: weight} — current-regime GMV
    theta: dict,
) -> dict:
    """Grade how far the efficient frontier has shifted under the current regime.

    Measurement: Euclidean distance between the all-regime GMV weight mix
    and the current-regime GMV weight mix.

    Score mapping:
      distance ≤ 0.05  → 5.0  (identical — regime hasn't shifted the frontier)
      distance ≤ 0.15  → 4.0  (minor rotation)
      distance ≤ 0.25  → 3.0  (moderate shift — sleeves changing)
      distance ≤ 0.50  → 1.5  (large shift — different optimal mix)
      distance >  0.50  → 0.5  (complete dislocation)

    Fail-open: missing either GMV → N/A.
    """
    if not gmv_all or not gmv_current:
        return {"composite_grade": "N/A", "composite_score": 0.0,
                "severity": "green"}

    distance = round(_euclidean_distance(gmv_all, gmv_current), 4)

    if distance <= 0.05:
        score = 5.0
    elif distance <= 0.15:
        score = 4.0
    elif distance <= 0.25:
        score = 3.0
    elif distance <= 0.50:
        score = 1.5
    else:
        score = 0.5

    score = round(score, 2)
    letter = _score_to_letter(score, theta["letter_score_bounds"])
    return {
        "composite_grade": letter,
        "composite_score": score,
        "severity": _severity(score, theta),
        "distance": distance,
        "n_tickers": len(set(gmv_all) | set(gmv_current)),
    }


def grade_tangency(
    sharpe_all: Optional[float],        # long-run tangency Sharpe
    sharpe_current: Optional[float],    # current-regime tangency Sharpe
    theta: dict,
) -> dict:
    """Grade the change in tangency portfolio performance under the current regime.

    Measurement: current tangency Sharpe / all-regime tangency Sharpe.
    A value of 1.0 means the tangency portfolio is UNCHANGED;
    values above 1.0 mean the regime IMPROVES the tangency (rare —
    caps at score 5.0/A); lower values mean the frontier has compressed.

    Score mapping (degradation direction; improvement capped at 5.0):
      ratio ≥ 0.95  → 5.0  (no change, or improvement — capped)
      ratio ≥ 0.80  → 4.0  (slight compression)
      ratio ≥ 0.65  → 3.0  (moderate — Sharpe down ~⅓)
      ratio ≥ 0.50  → 2.0  (significant — half the risk-adjusted return)
      ratio <  0.50  → 1.0  (severe — current regime destroys risk premia)

    Fail-open: missing either Sharpe → N/A.
    """
    if sharpe_all is None or sharpe_current is None:
        return {"composite_grade": "N/A", "composite_score": 0.0,
                "severity": "green"}
    if sharpe_all <= 0:
        return {"composite_grade": "N/A", "composite_score": 0.0,
                "severity": "green", "reason": "all-regime Sharpe ≤ 0 — "
                "regime comparison undefined"}

    ratio = round(sharpe_current / sharpe_all, 4)

    if ratio >= 0.95:
        score = 5.0
    elif ratio >= 0.80:
        score = 4.0
    elif ratio >= 0.65:
        score = 3.0
    elif ratio >= 0.50:
        score = 2.0
    else:
        score = 1.0

    score = round(score, 2)
    letter = _score_to_letter(score, theta["letter_score_bounds"])
    return {
        "composite_grade": letter,
        "composite_score": score,
        "severity": _severity(score, theta),
        "sharpe_all": round(sharpe_all, 4),
        "sharpe_current": round(sharpe_current, 4),
        "ratio": ratio,
    }


def grade_policy_gap(
    policy_weights: Dict[str, float],           # policy target weights
    current_tangency_weights: Dict[str, float], # current-regime tangency
    theta: dict,
) -> dict:
    """Grade how far the policy allocation is from the current-regime optimum.

    Measurement: Euclidean distance between the policy allocation and the
    tangency portfolio of the current regime's efficient frontier.
    A large distance means the policy was designed for a different regime
    than the one the portfolio is living in right now.

    Score mapping:
      distance ≤ 0.05  → 5.0  (policy on the money)
      distance ≤ 0.15  → 4.0  (close)
      distance ≤ 0.25  → 3.0  (moderate gap — policy needs a tilt)
      distance ≤ 0.50  → 1.5  (large gap — policy is for another regime)
      distance >  0.50  → 0.5  (policy disconnected — consider re-anchoring)

    Fail-open: missing either weight set → N/A.
    """
    if not policy_weights or not current_tangency_weights:
        return {"composite_grade": "N/A", "composite_score": 0.0,
                "severity": "green"}

    distance = round(
        _euclidean_distance(policy_weights, current_tangency_weights), 4)

    if distance <= 0.05:
        score = 5.0
    elif distance <= 0.15:
        score = 4.0
    elif distance <= 0.25:
        score = 3.0
    elif distance <= 0.50:
        score = 1.5
    else:
        score = 0.5

    score = round(score, 2)
    letter = _score_to_letter(score, theta["letter_score_bounds"])
    return {
        "composite_grade": letter,
        "composite_score": score,
        "severity": _severity(score, theta),
        "distance": distance,
        "n_policy_tickers": len(policy_weights),
        "n_frontier_tickers": len(current_tangency_weights),
    }


def grade_corr_structure(
    stock_bond_corr: Optional[float],  # 60d rolling corr (SPY / TLT)
    current_regime: str,               # "R1" | "R2" | "R3" | "R4"
    theta: dict,
) -> dict:
    """Grade whether the stock-bond correlation matches regime expectations.

    Regime expectation (research doc §2.1):
      R1 (Expansion), R3 (Recession): bonds hedge stocks → corr < 0 expected
      R2 (Overheating), R4 (Stagflation): diversification broken → corr ≥ 0

    DESIGN DECISION (frontier, 2026-08-09): this sub-axis grades against
    the REGIME expectation, not the policy's assumption. A 60/40 policy
    presumes negative correlation in all regimes — but the regime axis
    exists to flag when that assumption is wrong. When R2 says "bonds
    don't hedge" and the data confirms it (+corr), that's honest —
    grade A. The policy disconnect is caught by the policy_gap sub-axis
    (which would grade F for a 60/40 in R2 if risk premia differ enough).

    Score:
      5.0 — corr correctly signed, magnitude normal
      3.0 — corr anomalous (wrong sign for regime)
      1.0 — corr anomalous AND extreme (|corr| > 0.5 against expectation)

    Fail-open: missing correlation → N/A.
    """
    if stock_bond_corr is None:
        return {"composite_grade": "N/A", "composite_score": 0.0,
                "severity": "green"}

    corr = stock_bond_corr
    expects_negative = current_regime in ("R1", "R3")
    sign_ok = (corr < 0) if expects_negative else (corr >= 0)

    if sign_ok:
        score = 5.0
    elif abs(corr) > 0.5:
        score = 1.0  # extreme misalignment
    else:
        score = 3.0  # wrong sign but modest

    score = round(score, 2)
    letter = _score_to_letter(score, theta["letter_score_bounds"])
    return {
        "composite_grade": letter,
        "composite_score": score,
        "severity": _severity(score, theta),
        "stock_bond_corr": round(corr, 4),
        "expects_negative": expects_negative,
    }


# =============================================================================
# F3 — Composite merger (fail-open: missing sub-grade → zero-weight, N/A)
# =============================================================================

REGIME_AXIS_WEIGHTS = {
    "frontier_shift": 0.25,
    "tangency_degradation": 0.25,
    "policy_gap": 0.25,
    "corr_structure": 0.25,
}


def merge_regime_grade(levels: Dict[str, dict], theta: dict) -> dict:
    """Weighted composite regime grade from four sub-grades.

    Weights: 25% each (equal — all four dimensions matter equally for
    measuring how different the current regime is from the policy's baseline).

    Missing or errored sub-grades are zero-weighted (fail-open);
    do NOT keep the weight in the denominator (data gap penalty pitfall —
    same as drift/tax axes).
    """
    weights = theta.get("regime_axis_weights", REGIME_AXIS_WEIGHTS)
    axis_map = {
        "frontier_shift": "frontier_shift",
        "tangency_degradation": "tangency",
        "policy_gap": "policy_gap",
        "corr_structure": "corr_structure",
    }

    numerator = 0.0
    denominator = 0.0
    sub_grades = {}

    for key, label in axis_map.items():
        w = weights.get(key, 0.0)
        source = levels.get(label, {})
        if (isinstance(source, dict) and "composite_score" in source
                and source.get("composite_grade") != "N/A"):
            score = source["composite_score"]
            numerator += w * score
            denominator += w
            sub_grades[label] = {
                "grade": source["composite_grade"],
                "score": score,
                "weight": w,
                "severity": source.get("severity", "green"),
            }
        else:
            sub_grades[label] = {"grade": "N/A", "score": 0.0,
                                 "weight": w, "severity": "green"}

    if denominator > 0:
        composite_score = round(numerator / denominator, 2)
        composite_letter = _score_to_letter(
            composite_score, theta["letter_score_bounds"])
    else:
        composite_score = 0.0
        composite_letter = "N/A"

    # Worst-severity roll-up (red > orange > yellow > green)
    worst_severity = "green"
    sev_order = {"red": 3, "orange": 2, "yellow": 1, "green": 0}
    for sg in sub_grades.values():
        if sev_order.get(sg["severity"], 0) > sev_order.get(worst_severity, 0):
            worst_severity = sg["severity"]

    return {
        "composite_regime_grade": composite_letter,
        "composite_regime_score": composite_score,
        "severity": worst_severity,
        "axis_weights": weights,
        "sub_grades": sub_grades,
    }


# =============================================================================
# F4 — Regime tweak-list generator (frontier-owned — the language mapping)
# =============================================================================

def generate_regime_tweaks(levels: Dict[str, dict], theta: dict) -> List[dict]:
    """Ranked regime tweaks from the four sub-grade results.

    Each tweak: {axis, sub_axis, severity, recommended_action, rationale}.
    Severity ladder: green (info) → yellow (monitor) → orange (action) →
    red (re-anchor).

    Sorted by severity (red first), then by score (worst first).
    """
    # Guard division-by-zero / NaN: use the level's already-computed
    # score, distance, and ratio values — never re-derive from measurement.
    tweaks = []

    # ── Frontier shift ──────────────────────────────────────────────
    fs = levels.get("frontier_shift", {})
    if fs.get("composite_grade") not in (None, "N/A"):
        dist = fs.get("distance", 0)
        if fs.get("composite_score", 5.0) < 4.5:
            sev = "red" if dist > 0.50 else (
                "orange" if dist > 0.25 else "yellow")
            tweaks.append({
                "axis": "regime",
                "sub_axis": "frontier_shift",
                "severity": sev,
                "sigma": None,
                "recommended_action": (
                    f"Frontier shift {dist:.1%} from all-regime GMV — "
                    f"the current regime demands a different optimal mix. "
                    f"Consider rebalancing toward the current-regime frontier."
                ),
                "rationale": (
                    f"Efficient frontier GMV weight mix has shifted "
                    f"{dist:.1%} (Euclidean distance) from the long-run "
                    f"average. The current macro regime is demanding different "
                    f"asset sleeves than the policy was calibrated for."
                ),
            })

    # ── Tangency degradation ────────────────────────────────────────
    td = levels.get("tangency", {})
    if td.get("composite_grade") not in (None, "N/A"):
        ratio = td.get("ratio", 1.0)
        if td.get("composite_score", 5.0) < 4.5:
            sev = "red" if ratio < 0.50 else (
                "orange" if ratio < 0.65 else "yellow")
            tweaks.append({
                "axis": "regime",
                "sub_axis": "tangency_degradation",
                "severity": sev,
                "sigma": None,
                "recommended_action": (
                    f"Tangency Sharpe degraded to {td.get('sharpe_current', 0):.2f} "
                    f"(down {1 - ratio:.0%} from all-regime). Consider reducing "
                    f"risk exposure — the current regime is compressing risk "
                    f"premia."
                ),
                "rationale": (
                    f"The tangency portfolio's risk-adjusted return in the "
                    f"current regime ({td.get('sharpe_current', 0):.2f}) is "
                    f"{1 - ratio:.0%} below the all-regime average "
                    f"({td.get('sharpe_all', 0):.2f}). The market is paying "
                    f"less per unit of risk right now."
                ),
            })

    # ── Policy gap ──────────────────────────────────────────────────
    pg = levels.get("policy_gap", {})
    if pg.get("composite_grade") not in (None, "N/A"):
        dist = pg.get("distance", 0)
        if pg.get("composite_score", 5.0) < 4.5:
            sev = "red" if dist > 0.50 else (
                "orange" if dist > 0.25 else "yellow")
            tweaks.append({
                "axis": "regime",
                "sub_axis": "policy_gap",
                "severity": sev,
                "sigma": None,
                "recommended_action": (
                    f"Policy allocation is {dist:.1%} from the current-regime "
                    f"tangency portfolio. The policy was designed for a "
                    f"different macro environment — consider a regime-aligned "
                    f"tilt toward the current frontier."
                ),
                "rationale": (
                    f"The policy weights were calibrated under a different "
                    f"macro regime. The current regime's efficient frontier "
                    f"has a tangency mix {dist:.1%} away from the policy. "
                    f"Regime-conditional rebalancing may improve risk-adjusted "
                    f"returns."
                ),
            })

    # ── Correlation structure ───────────────────────────────────────
    cs = levels.get("corr_structure", {})
    if cs.get("composite_grade") not in (None, "N/A"):
        corr = cs.get("stock_bond_corr", 0) or 0
        if cs.get("composite_score", 5.0) < 4.5:
            expects_neg = cs.get("expects_negative", True)
            expected = "negative (bonds hedge)" if expects_neg else \
                       "positive (diversification broken)"
            sev = "red" if abs(corr) > 0.5 else "yellow"
            tweaks.append({
                "axis": "regime",
                "sub_axis": "corr_structure",
                "severity": sev,
                "sigma": None,
                "recommended_action": (
                    f"Stock-bond correlation ({corr:+.2f}) does not match "
                    f"regime expectation ({expected}). "
                    f"Diversification assumptions may be invalid."
                ),
                "rationale": (
                    f"The current regime expects {expected} stock-bond "
                    f"correlation, but the observed 60d correlation is "
                    f"{corr:+.2f}. The traditional 60/40 stock/bond "
                    f"diversification benefit may not hold."
                ),
            })

    # Sort: red first, then orange, then yellow; within tier, by score
    sev_order = {"red": 3, "orange": 2, "yellow": 1, "green": 0}
    tweaks.sort(
        key=lambda t: (
            -sev_order.get(t["severity"], 0),
            t.get("score", 5.0),
        )
    )
    return tweaks


# =============================================================================
# F5 — Enhancer multiplier (frontier-owned — the money formula)
# =============================================================================

def compute_enhancer_multiplier(regime_composite_score: float) -> float:
    """Compute the regime enhancer multiplier on the drift+concentration+tax
    composite average.

    The regime axis adjusts for the macro environment but never penalises
    below half-value (Hong, 2026-08-09). The PM didn't cause stagflation.

    Formula:  multiplier = max(0.5, 1.0 − (5.0 − score) / 10.0)

      score 5.0 → multiplier 1.00  (regime optimal — no penalty)
      score 4.0 → multiplier 0.90  (minor shift)
      score 3.0 → multiplier 0.80  (moderate gap)
      score 2.0 → multiplier 0.70  (significant — regime is drifting)
      score 1.0 → multiplier 0.60  (severe — regime is working against you)
      score 0.0 → multiplier 0.50  (hard floor — PM didn't cause stagflation)

    Returns:
        float in [0.5, 1.0]
    """
    if regime_composite_score is None:
        return 1.0  # no regime data → no penalty
    multiplier = 1.0 - (5.0 - regime_composite_score) / 10.0
    return round(max(0.5, min(1.0, multiplier)), 2)


# =============================================================================
# F6 — Pipeline orchestrator (frontier-owned — the contract for cheap model)
# =============================================================================

def run_regime_grade(
    # Checker outputs (dicts — Phase 2b cheap model provides these)
    frontier_shift_result: dict,
    tangency_result: dict,
    policy_gap_result: dict,
    corr_structure_result: dict,
    current_regime: str,                # from RegimeClassifier
    theta: Optional[dict] = None,
) -> dict:
    """Run the full regime axis grade pipeline.

    Architecture per axis (research doc §7, NS-5 skill):
      checker (measure) → grade_* → merge → generate_tweaks

    Exposes raw measurements under 'levels' for the dashboard detail rows.

    Args:
        frontier_shift_result:  from check_frontier_shift() — {gmv_all, gmv_current}
        tangency_result:        from check_tangency() — {sharpe_all, sharpe_current}
        policy_gap_result:      from check_policy_gap() —
                                {policy_weights, current_tangency_weights}
        corr_structure_result:  from check_corr_structure() —
                                {stock_bond_corr, current_regime}
        current_regime:         "R1" | "R2" | "R3" | "R4"
        theta:                  owner parameter vector (load_theta())

    Returns:
        {sub_grades: {label: {grade, score, weight, severity}},
         composite_regime_grade, composite_regime_score, severity,
         tweaks: [{axis, sub_axis, severity, recommended_action, ...}],
         levels: {frontier_shift: checker_output, ...}}
    """
    if theta is None:
        theta = theta_mod.THETA_DEFAULTS

    regime_theta = theta.get("regime", {})
    if regime_theta is None:
        return {
            "composite_regime_grade": "N/A",
            "composite_regime_score": 0.0,
            "severity": "green",
            "sub_grades": {},
            "tweaks": [],
            "levels": {},
            "error": "regime axis disabled — configure Θ.regime",
        }

    # ── Grade each sub-axis ──────────────────────────────────────────
    frontier_grade = grade_frontier_shift(
        frontier_shift_result.get("gmv_all", {}),
        frontier_shift_result.get("gmv_current", {}),
        theta,
    )
    tangency_grade = grade_tangency(
        tangency_result.get("sharpe_all"),
        tangency_result.get("sharpe_current"),
        theta,
    )
    policy_grade = grade_policy_gap(
        policy_gap_result.get("policy_weights", {}),
        policy_gap_result.get("current_tangency_weights", {}),
        theta,
    )
    corr_grade = grade_corr_structure(
        corr_structure_result.get("stock_bond_corr"),
        current_regime,
        theta,
    )

    levels = {
        "frontier_shift": {**frontier_shift_result, **frontier_grade},
        "tangency":       {**tangency_result,       **tangency_grade},
        "policy_gap":     {**policy_gap_result,     **policy_grade},
        "corr_structure": {**corr_structure_result, **corr_grade},
    }

    # ── Merge ────────────────────────────────────────────────────────
    merged = merge_regime_grade(levels, theta)

    # ── Tweaks ───────────────────────────────────────────────────────
    tweaks = generate_regime_tweaks(levels, theta)

    # ── Enhancer ─────────────────────────────────────────────────────
    enhancer = compute_enhancer_multiplier(merged["composite_regime_score"])

    return {
        "sub_grades": merged["sub_grades"],
        "composite_regime_grade": merged["composite_regime_grade"],
        "composite_regime_score": merged["composite_regime_score"],
        "severity": merged["severity"],
        "enhancer": enhancer,
        "tweaks": tweaks,
        "levels": levels,
    }
