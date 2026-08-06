#!/usr/bin/env python3
"""
NS-5 Drift Axis — grading, composite merger, and tweak language.
Money-path: methodology, thresholds, and language (frontier-owned).
Checker functions (deterministic math) are in drift_checkers.py (cheap).

Four drift levels per research doc §3.2:
  Level 1 — Weight drift: position weight vs policy band (±20% relative)
  Level 2 — Risk drift:   trailing vol/VaR/CVaR vs risk budget
  Level 3 — Style/factor drift: factor β shift + QQQ correlation
  Level 4 — Frontier drift: frontier shifted (Sharpe degradation, tangency
             mix change, stock-bond correlation sign flip)
"""
from __future__ import annotations

from typing import Dict, List

import theta as theta_mod


# =========================================================================
# F2 — Per-level grade functions (frontier-owned: score → letter + severity)
# =========================================================================

def _score_to_letter(score: float, bounds: List) -> str:
    """Map a numeric score to a letter via descending thresholds (A=highest)."""
    for threshold, letter in bounds:
        if score >= threshold:
            return letter
    return bounds[-1][1] if bounds else "N/A"


def _letter_to_score(letter: str) -> float:
    """Coarse: A=5, B=4, C=3, D=2, F=1. Used for composite weighting."""
    return {"A": 5.0, "B": 4.0, "C": 3.0, "D": 2.0, "F": 1.0}.get(letter, 0.0)


def _severity(score: float, theta: dict) -> str:
    """Map a numeric score to a severity label via drift_severity_bounds."""
    bounds = theta.get("drift_severity_bounds", [(0, "red"), (2.0, "orange"), (3.5, "yellow"), (5.0, "green")])
    for threshold, sev in bounds:
        if score >= threshold:
            return sev
    return bounds[-1][1]


def grade_weight_drift(flagged_count: int, total_positions: int,
                       theta: dict) -> dict:
    """
    Grade weight drift from flagged-position count.

    Rationale: each position outside ±band is one violation. The grade
    reflects how many violations, not the dollar magnitude (the checker
    has the per-position details; the grade is the roll-up).
    """
    if total_positions == 0:
        return {"composite_grade": "N/A", "composite_score": 0.0, "severity": "green", "flagged_count": 0}

    ratio = flagged_count / total_positions
    # Score: linearly from 5.0 (0% flagged) to 1.0 (100% flagged)
    score = round(5.0 - 4.0 * ratio, 2)
    letter = _score_to_letter(score, theta["letter_score_bounds"])
    return {
        "composite_grade": letter,
        "composite_score": score,
        "severity": _severity(score, theta),
        "flagged_count": flagged_count,
        "total_positions": total_positions,
    }


def grade_risk_drift(vol_ratio: float,  # trailing_vol / long_run_vol
                     var_breach: bool,    # VaR(95%) exceeds limit
                     cvar_breach: bool,   # CVaR(95%) exceeds limit
                     theta: dict) -> dict:
    """
    Grade risk drift from vol spike ratio + VaR/CVaR breaches.

    vol_ratio > 1.5 → red; > 1.2 → orange; > 1.0 → yellow; ≤ 1.0 → green.
    VaR or CVaR breach → drops one extra severity tier.
    """
    if vol_ratio <= 1.0 and not var_breach and not cvar_breach:
        score = 5.0
    elif vol_ratio <= 1.0:
        score = 4.0       # vol fine but tail risk breached
    elif vol_ratio <= 1.2:
        score = 3.0       # mild vol spike
    elif vol_ratio <= 1.5:
        score = 2.0       # elevated vol
    else:
        score = 1.0       # severe vol spike

    if var_breach or cvar_breach:
        score = max(1.0, score - 1.0)   # tail risk penalty

    score = round(score, 2)
    letter = _score_to_letter(score, theta["letter_score_bounds"])
    return {
        "composite_grade": letter,
        "composite_score": score,
        "severity": _severity(score, theta),
        "vol_ratio": round(vol_ratio, 3),
        "var_breach": var_breach,
        "cvar_breach": cvar_breach,
    }


def grade_style_drift(factor_deviations: List[dict],   # [{factor, delta_sigma, flagged}]
                      qqq_corr: float,
                      theta: dict) -> dict:
    """
    Grade style/factor drift from factor β shifts + QQQ correlation.

    Each flagged factor (|Δβ|/se > tolerance) adds a penalty.
    QQQ correlation > 0.90 → independent flag ("this IS QQQ").
    """
    flagged = [f for f in factor_deviations if f.get("flagged")]
    n_flagged = len(flagged)
    n_total = len(factor_deviations) or 1

    # Base score from flagged factor count
    flag_ratio = n_flagged / n_total
    base_score = round(5.0 - 4.0 * flag_ratio, 2)

    # QQQ penalty: ≥0.90 drops one extra tier
    if qqq_corr >= theta["style_tolerance"]["qqq_corr_threshold"]:
        base_score = max(1.0, base_score - 1.0)

    score = round(base_score, 2)
    letter = _score_to_letter(score, theta["letter_score_bounds"])
    return {
        "composite_grade": letter,
        "composite_score": score,
        "severity": _severity(score, theta),
        "flagged_factors": [f["factor"] for f in flagged],
        "n_flagged": n_flagged,
        "qqq_corr": round(qqq_corr, 4),
        "qqq_flagged": qqq_corr >= theta["style_tolerance"]["qqq_corr_threshold"],
    }


def grade_frontier_drift(sharpe_degradation: float,   # long_run − trailing
                         tangency_shift: float,         # max-weight diff in tangency mix
                         bond_corr_sign_flipped: bool,
                         theta: dict) -> dict:
    """
    Grade frontier drift — how much has the efficient frontier shifted?

    sharpe_degradation > 0.15 + tangency_shift > 0.15 → red
    either > threshold → orange
    bond_corr_sign_flip → independent flag (drops one extra tier)
    neither → green
    """
    sharpe_flag = sharpe_degradation >= theta["frontier_thresholds"]["sharpe_degradation"]
    tangency_flag = tangency_shift >= theta["frontier_thresholds"]["tangency_shift"]

    if sharpe_flag and tangency_flag:
        score = 1.0   # both shifted — critical
    elif sharpe_flag or tangency_flag:
        score = 2.5   # one shifted — elevated
    else:
        score = 4.5   # clean — borderline A

    if bond_corr_sign_flipped:
        score = max(1.0, score - 1.0)   # bond diversification broke

    score = round(score, 2)
    letter = _score_to_letter(score, theta["letter_score_bounds"])
    return {
        "composite_grade": letter,
        "composite_score": score,
        "severity": _severity(score, theta),
        "sharpe_degradation": round(sharpe_degradation, 4),
        "tangency_shift": round(tangency_shift, 4),
        "sharpe_flagged": sharpe_flag,
        "tangency_flagged": tangency_flag,
        "bond_corr_sign_flipped": bond_corr_sign_flipped,
    }


# =========================================================================
# F3 — Composite drift grade merger (frontier-owned)
# =========================================================================

def merge_drift_grade(levels: Dict[str, dict], theta: dict) -> dict:
    """
    Weighted composite drift grade from four sub-grades.

    Weights per theta.drift_axis_weights:
      weight_drift: 0.15, risk_drift: 0.25, style_drift: 0.30, frontier_drift: 0.30
    Missing or errored sub-grades are zero-weighted (fail-open).
    """
    weights = theta.get("drift_axis_weights", {})
    axis_map = {
        "weight_drift": "weight",
        "risk_drift": "risk",
        "style_drift": "style",
        "frontier_drift": "frontier",
    }

    numerator = 0.0
    denominator = 0.0
    sub_grades = {}

    for key, label in axis_map.items():
        source = levels.get(label, {})
        w = weights.get(key, 0.0)
        if isinstance(source, dict) and "composite_score" in source:
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
            sub_grades[label] = {"grade": "N/A", "score": 0.0, "weight": w, "severity": "green"}

    if denominator > 0:
        composite_score = round(numerator / denominator, 2)
    else:
        composite_score = 0.0

    composite_letter = _score_to_letter(composite_score, theta["letter_score_bounds"])
    worst_severity = "green"
    for sg in sub_grades.values():
        if sg["severity"] in ("red",):
            worst_severity = "red"
        elif sg["severity"] == "orange" and worst_severity not in ("red",):
            worst_severity = "orange"
        elif sg["severity"] == "yellow" and worst_severity not in ("red", "orange"):
            worst_severity = "yellow"

    return {
        "composite_drift_grade": composite_letter,
        "composite_drift_score": composite_score,
        "severity": worst_severity,
        "axis_weights": weights,
        "sub_grades": sub_grades,
    }


# =========================================================================
# F4 — Drift tweak-list generator (frontier-owned — the language mapping)
# =========================================================================

def generate_drift_tweaks(levels: Dict[str, dict], theta: dict) -> List[dict]:
    """
    Ranked drift tweak list from the four sub-grade results.

    Each tweak: {axis, level, severity, recommended_action, rationale}
    Sorted by severity (red first, then orange, yellow, green).
    Only flagged items produce tweaks.
    """
    tweaks = []
    band = theta.get("drift_band", 0.20)
    band_pct = f"{int(band * 100)}%"

    # --- Weight drift tweaks ---
    wd = levels.get("weight", {})
    for item in wd.get("items", []):
        if not item.get("flagged"):
            continue
        ratio = item.get("ratio", 0)
        sev = "critical" if ratio > 0.5 else ("high" if ratio > 0.3 else "medium")
        tweaks.append({
            "axis": "drift",
            "level": "weight_drift",
            "severity": sev,
            "ticker": item.get("ticker", ""),
            "current_weight": item.get("actual", 0),
            "policy_weight": item.get("policy", 0),
            "ratio": round(ratio, 3),
            "recommended_action": (
                f"rebalance {item['ticker']} from "
                f"{item['actual']:.1%} to ≤{item['policy']:.1%} "
                f"(±{band_pct} relative band breach)"
            ),
            "rationale": (
                f"{item['ticker']} weight at {item['actual']:.1%}, "
                f"policy target {item['policy']:.1%} — "
                f"{item['actual']/item['policy']:.1%}× target, "
                f"outside ±{band_pct} tolerance"
            ),
        })

    # --- Risk drift tweaks ---
    rd = levels.get("risk", {})
    if rd.get("composite_score", 5) < 4:
        sev = "critical" if rd.get("composite_score", 5) <= 2 else "high"
        tweaks.append({
            "axis": "drift",
            "level": "risk_drift",
            "severity": sev,
            "trailing_vols": rd.get("trailing_vols", {}),
            "long_run_vol": rd.get("long_run_vol", 0),
            "var_breach": rd.get("var_breach", False),
            "cvar_breach": rd.get("cvar_breach", False),
            "recommended_action": vol_target_recommendation(rd),
            "rationale": vol_target_rationale(rd),
        })
    elif rd.get("var_breach") or rd.get("cvar_breach"):
        tweaks.append({
            "axis": "drift",
            "level": "risk_drift",
            "severity": "medium",
            "recommended_action": (
                "VaR/CVaR breach while vol is within band — "
                "fat-tail risk elevated. Review tail hedges or reduce size."
            ),
            "rationale": "trailing VaR/CVaR exceeds risk budget; vol appears normal but tail risk is not.",
        })

    # --- Style drift tweaks ---
    sd = levels.get("style", {})
    for fc in sd.get("factor_deviations", []):
        if not fc.get("flagged"):
            continue
        sigma = fc.get("sigma", 0)
        sev = "critical" if sigma > 3 else ("high" if sigma > 2 else "medium")
        tweaks.append({
            "axis": "drift",
            "level": "style_drift",
            "severity": sev,
            "factor": fc.get("factor", ""),
            "current_beta": fc.get("current_beta", 0),
            "policy_beta": fc.get("policy_beta", 0),
            "sigma": round(sigma, 2),
            "recommended_action": factor_drift_action(fc),
            "rationale": (
                f"{fc['factor']} loading drifted from {fc['policy_beta']:.2f} "
                f"to {fc['current_beta']:.2f} ({sigma:.1f}σ). "
                f"Portfolio has accumulated a different factor exposure than the policy target."
            ),
        })
    if sd.get("qqq_flagged"):
        tweaks.append({
            "axis": "drift",
            "level": "style_drift",
            "severity": "high",
            "recommended_action": (
                f"portfolio {sd['qqq_corr']:.2f} correlated to QQQ — "
                f"this portfolio IS the Nasdaq. The ticker diversity is an illusion."
            ),
            "rationale": (
                f"QQQ correlation {sd['qqq_corr']:.3f} exceeds "
                f"{theta['style_tolerance']['qqq_corr_threshold']} threshold. "
                f"Diversification across tickers masks one-bet concentration."
            ),
        })
    if not sd.get("factor_deviations") and sd.get("qqq_flagged"):
        pass  # already added

    # --- Frontier drift tweaks ---
    fd = levels.get("frontier", {})
    if fd.get("composite_score", 5) < 4:
        sev = "critical" if fd.get("composite_score", 5) <= 2 else "high"
        actions = []
        if fd.get("sharpe_flagged"):
            actions.append(
                f"Sharpe degraded {fd.get('sharpe_degradation', 0):.2f} "
                f"from long-run ({theta['frontier_thresholds']['sharpe_degradation']} threshold)"
            )
        if fd.get("tangency_flagged"):
            actions.append(
                f"tangency portfolio mix shifted {fd.get('tangency_shift', 0):.1%}"
            )
        if fd.get("bond_corr_sign_flipped"):
            actions.append("stock-bond correlation sign flipped — bonds no longer diversifying")
        tweaks.append({
            "axis": "drift",
            "level": "frontier_drift",
            "severity": sev,
            "recommended_action": "revisit target allocation — the frontier has shifted. " + "; ".join(actions),
            "rationale": (
                f"trailing 2yr frontier differs from long-run frontier. "
                f"Sharpe: {fd.get('sharpe_trailing', 0):.3f} (trailing) vs "
                f"{fd.get('sharpe_long_run', 0):.3f} (long-run). "
                f"The μ, Σ the policy was built on no longer describe these assets."
            ),
        })

    # Sort: critical/high first, then medium
    severity_order = {"critical": 0, "high": 1, "medium": 2}
    tweaks.sort(key=lambda t: severity_order.get(t.get("severity", "medium"), 3))
    return tweaks


# =========================================================================
# Helper language generators
# =========================================================================

def vol_target_recommendation(rd: dict) -> str:
    """Actionable vol-target recommendation from risk drift result."""
    tv = rd.get("trailing_vols", {})
    lr = rd.get("long_run_vol", 0)
    # Use the worst trailing window for the recommendation
    worst_win = max((k for k in tv if tv[k]), default=None, key=lambda k: tv[k])
    if worst_win and lr > 0:
        return (
            f"trailing {worst_win} vol {tv[worst_win]:.1%} vs long-run avg {lr:.1%} "
            f"— scale down positions proportionally to bring vol inside risk budget"
        )
    return "vol spike detected — review position sizes"


def vol_target_rationale(rd: dict) -> str:
    """Rationale for vol-target recommendation."""
    tv = rd.get("trailing_vols", {})
    lr = rd.get("long_run_vol", 0)
    parts = []
    for w in sorted(tv):
        if tv[w]:
            parts.append(f"{w}: {tv[w]:.1%}")
    return (f"trailing vol {' / '.join(parts)} vs long-run {lr:.1%}. "
            f"Risk budget σ*={theta_mod.THETA_DEFAULTS['risk_budget']['target_vol']:.1%}.")


def factor_drift_action(fc: dict) -> str:
    """Direction-specific recommendation from a factor β drift."""
    factor = fc.get("factor", "")
    current = fc.get("current_beta", 0)
    policy = fc.get("policy_beta", 0)
    direction = "overweight" if current > policy else "underweight"

    hints = {
        "HML": "growth-heavy tilt — rotate from growth into value/defensive names",
        "MOM": "momentum-chasing — trim recent winners, add reversion candidates",
        "SMB": "small-cap tilt — shift allocation between large and small-cap",
        "MKT": "market-β shift — adjust equity allocation to match policy exposure",
        "DUR": "duration sensitivity — rebalance bond holdings to match policy duration",
    }
    hint = hints.get(factor, "")

    return (f"reduce {direction} exposure to {factor} factor: "
            f"β drifted from {policy:.2f} to {current:.2f}"
            f"{' — ' + hint if hint else ''}")