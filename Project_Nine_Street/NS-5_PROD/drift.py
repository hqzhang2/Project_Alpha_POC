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

from typing import Dict, List, Optional

import pandas as pd

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


def compute_portfolio_composite(axis_results: dict, letter_bounds: list) -> dict:
    """Portfolio composite: base × regime enhancer (v3.3.1, design A).

    base = mean of active non-regime composite scores (concentration +
    drift; N/A/None excluded). Regime is an ENHANCER on the composite:
    enhancer ∈ [0.5, 1.0], never pulls below the other axes' average.
    Fail-open: regime missing/disabled/N-A → enhancer = 1.0; no base
    scores → base/portfolio = None ("N/A"), never crashes.

    axis_results: the /api/grade result dict so far (concentration key is
    NESTED under result['concentration'] — the run_concentration_grade
    return shape; drift under result['drift']).
    """
    base_scores = []
    conc_res = axis_results.get("concentration")
    if isinstance(conc_res, dict) and conc_res.get("composite_concentration_score") is not None:
        base_scores.append(conc_res["composite_concentration_score"])
    drift_res = axis_results.get("drift")
    if isinstance(drift_res, dict) and drift_res.get("composite_drift_score") is not None:
        base_scores.append(drift_res["composite_drift_score"])

    base_score = round(sum(base_scores) / len(base_scores), 2) if base_scores else None
    base_grade = _score_to_letter(base_score, letter_bounds) if base_score is not None else "N/A"

    enhancer = 1.0
    regime_res = axis_results.get("regime")
    if isinstance(regime_res, dict) and regime_res.get("composite_regime_grade") != "N/A":
        enhancer = regime_res.get("enhancer", 1.0)

    portfolio_score = round(base_score * enhancer, 2) if base_score is not None else None
    portfolio_grade = _score_to_letter(portfolio_score, letter_bounds) if portfolio_score is not None else "N/A"

    return {
        "base_composite_score": base_score,
        "base_composite_grade": base_grade,
        "portfolio_composite_score": portfolio_score,
        "portfolio_composite_grade": portfolio_grade,
        "regime_enhancer_applied": enhancer,
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
        label = item.get("sector", item.get("ticker", ""))
        target_pct = f"{item['policy']:.1%}" if item.get('policy', 0) > 0 else "0% (no anchor)"
        mult = f"{item['actual']/item['policy']:.1%}× target, " if item.get('policy', 0) > 0 else "no policy anchor, "
        tweaks.append({
            "axis": "drift",
            "level": "weight_drift",
            "severity": sev,
            "ticker": label,
            "current_weight": item.get("actual", 0),
            "policy_weight": item.get("policy", 0),
            "ratio": round(ratio, 3),
            "recommended_action": (
                f"rebalance {label} from "
                f"{item['actual']:.1%} to ≤{target_pct} "
                f"(±{band_pct} relative band breach)"
            ),
            "rationale": (
                f"{label} weight at {item['actual']:.1%}, "
                f"policy target {target_pct} — "
                f"{mult}"
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


# =========================================================================
# C1–C4 — Drift checker functions (deterministic math — cheap-owned)
# Each returns {composite_grade, composite_score, severity, ...} compatible
# with merge_drift_grade() and generate_drift_tweaks().
# =========================================================================

def check_weight_drift(holdings_weights: Dict[str, float],
                       policy_weights: Dict[str, float],
                       theta: dict) -> Dict:
    """
    Level 1 — Weight drift: asset-class-level weight vs policy band.

    Aggregates ticker weights into sectors via theta['sector_map'], then
    rolls up into asset classes (Equity / Fixed-Income / Cash / Commodity)
    so that a stock portfolio of Sector-Tech + Sector-Healthcare compares
    meaningfully to an ETF policy of Equity-Large.  A ticker-level
    comparison is meaningless when the portfolio holds individual stocks
    and the policy is expressed in ETFs.
    """
    band = theta.get("drift_band", 0.20)
    sector_map = theta.get("sector_map", {})

    def _to_asset_class(sec):
        if sec.startswith("Sector-") or sec.startswith("Equity-") or sec == "Unknown":
            return "Equity"
        if sec.startswith("Fixed-Income"):
            return "Fixed-Income"
        return sec  # Cash, Commodity, or custom — pass through

    def _aggregate(weights):
        ac = {}
        for tk, w in weights.items():
            sec = sector_map.get(tk, "Unknown")
            cls = _to_asset_class(sec)
            ac[cls] = ac.get(cls, 0.0) + w
        return ac

    port_ac = _aggregate(holdings_weights)
    policy_ac = _aggregate(policy_weights)
    all_classes = sorted(set(port_ac) | set(policy_ac))

    items = []
    flagged_count = 0
    for cls in all_classes:
        actual = port_ac.get(cls, 0.0)
        target = policy_ac.get(cls, 0.0)
        if target > 0:
            ratio = abs(actual - target) / target
        else:
            ratio = 1.0 if actual > 0 else 0.0
        flagged = ratio > band
        if flagged:
            flagged_count += 1
        items.append({
            "sector": cls,
            "actual": round(actual, 4),
            "policy": round(target, 4),
            "ratio": round(ratio, 3),
            "flagged": flagged,
        })

    grade = grade_weight_drift(flagged_count, len(all_classes), theta)
    grade["items"] = items
    return grade


def check_risk_drift(portfolio_returns, theta: dict) -> Dict:
    """
    Level 2 — Risk drift: trailing vol/VaR/CVaR vs long-run baseline.

    Trailing windows: 60d / 120d / 250d annualized vol vs full-sample
    long-run vol. VaR(95%)/CVaR(95%) from the most recent 250 days.
    vol_ratio = worst trailing window / long-run; > 1.5 → spike.
    """
    if portfolio_returns is None or len(portfolio_returns) < 60:
        return {"composite_grade": "N/A", "composite_score": 0.0,
                "severity": "green", "error": "insufficient return data"}

    rets = portfolio_returns.dropna()
    long_run_vol = float(rets.std() * (252 ** 0.5))

    trailing_vols = {}
    for win in (60, 120, 250):
        if len(rets) >= win:
            trailing_vols[f"{win}d"] = round(float(rets.iloc[-win:].std() * (252 ** 0.5)), 4)

    vol_ratio = 1.0
    if trailing_vols and long_run_vol > 0:
        vol_ratio = max(trailing_vols.values()) / long_run_vol

    # VaR/CVaR(95%) from the trailing 250 days (or full if shorter)
    tail = rets.iloc[-250:]
    var_95 = float(tail.quantile(0.05))
    cvar_95 = float(tail[tail <= var_95].mean()) if (tail <= var_95).any() else var_95

    budget = theta.get("risk_budget", {})
    var_breach = var_95 < budget.get("var_95_limit", -0.15)
    cvar_breach = cvar_95 < budget.get("cvar_95_limit", -0.22)

    grade = grade_risk_drift(vol_ratio, var_breach, cvar_breach, theta)
    grade["trailing_vols"] = trailing_vols
    grade["long_run_vol"] = round(long_run_vol, 4)
    grade["var_95"] = round(var_95, 4)
    grade["cvar_95"] = round(cvar_95, 4)
    return grade


def check_style_drift(portfolio_returns, factor_returns,
                      policy_beta: Dict[str, float], theta: dict) -> Dict:
    """
    Level 3 — Style/factor drift: β shift vs policy + QQQ correlation.

    OLS on the trailing 2yr window; |β_actual − β_policy| / se > tolerance
    → flagged factor. QQQ correlation > 0.90 → 'this IS QQQ' flag.
    """
    import regression

    if portfolio_returns is None or factor_returns is None or factor_returns.empty:
        return {"composite_grade": "N/A", "composite_score": 0.0,
                "severity": "green", "error": "insufficient data"}

    # Trailing window: last ~2 years of daily data (config-driven default 2)
    win_years = theta.get("factor_regression_window", 2)
    win_days = int(win_years * 252)
    rets = portfolio_returns.dropna()
    if len(rets) > win_days:
        rets = rets.iloc[-win_days:]

    result = regression.regress(rets, factor_returns)
    if result is None:
        return {"composite_grade": "N/A", "composite_score": 0.0,
                "severity": "green", "error": "regression failed"}

    tolerance = theta.get("style_tolerance", {}).get("factor_sigma", 1.5)
    factor_deviations = []
    for factor, actual_beta in result["beta"].items():
        policy_b = policy_beta.get(factor, 0.0)
        se = result.get("se", {}).get(factor, 0.0) or 0.0
        delta_sigma = abs(actual_beta - policy_b) / se if se > 0 else 0.0
        factor_deviations.append({
            "factor": factor,
            "current_beta": round(actual_beta, 4),
            "policy_beta": round(policy_b, 4),
            "sigma": round(delta_sigma, 2),
            "flagged": delta_sigma > tolerance,
        })

    # QQQ correlation (QQQ is the tech-growth benchmark — style proxy)
    qqq_corr = 0.0
    try:
        import data_fetcher
        qqq_closes = data_fetcher.get_closes(["QQQ"])
        if not qqq_closes.empty:
            qqq_rets = data_fetcher.compute_log_returns(qqq_closes[["QQQ"]]).iloc[:, 0]
            aligned = pd.concat([rets, qqq_rets], axis=1, join="inner").dropna()
            if len(aligned) > 30:
                qqq_corr = float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
    except Exception:
        qqq_corr = 0.0  # fail-open: no QQQ data → no QQQ flag

    grade = grade_style_drift(factor_deviations, qqq_corr, theta)
    grade["factor_deviations"] = factor_deviations
    return grade


def check_frontier_drift(closes, holdings_weights: Dict[str, float],
                         policy_weights: Dict[str, float], theta: dict) -> Dict:
    """
    Level 4 — Frontier drift: long-run vs trailing 2yr frontier.

    Sharpe degradation: long-run Sharpe of the policy portfolio vs trailing.
    Tangency shift: max weight diff in the max-Sharpe (tangency) portfolio.
    Bond flip: stock-bond correlation sign change on trailing window.
    """
    import frontier as frontier_mod
    import numpy as np

    tickers = list(holdings_weights.keys())
    if closes is None or closes.empty or len(closes) < 120:
        return {"composite_grade": "N/A", "composite_score": 0.0,
                "severity": "green", "error": "insufficient closes"}

    available = [t for t in tickers if t in closes.columns]
    if len(available) < 2:
        return {"composite_grade": "N/A", "composite_score": 0.0,
                "severity": "green", "error": "insufficient universe"}

    closes_f = closes[available].copy()
    trail_len = min(504, len(closes_f) // 2)  # trailing window ≈ 2yr (max)
    closes_trail = closes_f.iloc[-trail_len:]

    # --- Sharpe degradation: policy portfolio return series, both windows ---
    import data_fetcher
    import portfolio as portfolio_mod

    def _policy_sharpe(cdf):
        p_ret = portfolio_mod.build_portfolio_returns(policy_weights, closes=cdf)
        if p_ret is None or len(p_ret) < 30:
            return None
        mu = p_ret.mean() * 252
        sd = p_ret.std() * (252 ** 0.5)
        return (mu / sd) if sd > 0 else None

    sharpe_long = _policy_sharpe(closes_f)
    sharpe_trail = _policy_sharpe(closes_trail)
    degradation = 0.0
    if sharpe_long is not None and sharpe_trail is not None:
        degradation = max(0.0, sharpe_long - sharpe_trail)

    # --- Tangency (max-Sharpe) portfolio weights on both windows ---
    def _tangency_weights(cdf):
        rets = data_fetcher.compute_log_returns(cdf)
        if rets.empty or len(rets) < 60:
            return None
        mu = rets.mean().to_numpy() * 252
        cov = frontier_mod._cov_shrunk(rets)
        inv = np.linalg.pinv(cov)
        ones = np.ones(len(mu))
        w = inv @ mu
        denom = ones @ w
        if abs(denom) < 1e-12:
            return None
        w = w / denom
        w = np.clip(w, 0, 1)          # long-only flavor for comparison
        if w.sum() > 0:
            w = w / w.sum()
        return w

    w_long = _tangency_weights(closes_f)
    w_trail = _tangency_weights(closes_trail)
    tangency_shift = 0.0
    if w_long is not None and w_trail is not None:
        tangency_shift = float(np.max(np.abs(w_long - w_trail)))

    # --- Stock-bond correlation sign flip ---
    bond_flip = False
    corr_trail = None
    try:
        eq = "SPY" if "SPY" in closes_f.columns else ("QQQ" if "QQQ" in closes_f.columns else None)
        bd = "TLT" if "TLT" in closes_f.columns else None
        if eq and bd:
            r = data_fetcher.compute_log_returns(closes_f[[eq, bd]].dropna())
            if len(r) > 60:
                corr_long = float(r.iloc[:, 0].corr(r.iloc[:, 1]))
                r_t = data_fetcher.compute_log_returns(closes_trail[[eq, bd]].dropna())
                corr_trail = float(r_t.iloc[:, 0].corr(r_t.iloc[:, 1])) if len(r_t) > 30 else corr_long
                if abs(corr_long) > 0.1 and abs(corr_trail) > 0.1:
                    bond_flip = (corr_long * corr_trail) < 0
    except Exception:
        bond_flip = False  # fail-open

    grade = grade_frontier_drift(degradation, tangency_shift, bond_flip, theta)
    grade["sharpe_long_run"] = round(sharpe_long, 4) if sharpe_long is not None else None
    grade["sharpe_trailing"] = round(sharpe_trail, 4) if sharpe_trail is not None else None
    grade["bond_corr"] = round(corr_trail, 4) if corr_trail is not None else None
    return grade


# =========================================================================
# C7 — Drift pipeline: run all checkers + merge + tweaks
# =========================================================================

def run_drift_grade(holdings_weights: Dict[str, float],
                    policy_weights: Dict[str, float],
                    factor_returns=None,
                    closes=None,
                    theta: dict = None,
                    force_refresh: bool = False) -> Dict:
    """
    End-to-end drift grading: run 4 checkers → merge → tweaks.

    holdings_weights: {ticker: weight} (normalized)
    policy_weights:   {ticker: weight} — the policy target
    factor_returns:   optional pre-loaded factor DataFrame
    closes:           optional pre-loaded closes DataFrame
    """
    import data_fetcher
    import portfolio as portfolio_mod

    if theta is None:
        theta = theta_mod.load_theta()

    # Portfolio returns from holdings (for risk + style drift)
    if closes is None:
        all_tk = sorted(set(holdings_weights) | set(policy_weights))
        closes = data_fetcher.get_closes(all_tk, force_refresh=force_refresh)
    portfolio_returns = portfolio_mod.build_portfolio_returns(holdings_weights, closes=closes)

    # Policy β* for style drift (reuse concentration's policy derivation)
    import concentration
    policy_beta_result = concentration.compute_policy_beta(
        policy_weights, factor_returns=factor_returns, closes=closes,
        force_refresh=force_refresh)
    policy_beta = policy_beta_result.get("beta", {}) if isinstance(policy_beta_result, dict) else {}

    levels = {
        "weight": check_weight_drift(holdings_weights, policy_weights, theta),
        "risk": check_risk_drift(portfolio_returns, theta),
        "style": check_style_drift(portfolio_returns, factor_returns, policy_beta, theta),
        "frontier": check_frontier_drift(closes, holdings_weights, policy_weights, theta),
    }

    merged = merge_drift_grade(levels, theta)
    merged["tweaks"] = generate_drift_tweaks(levels, theta)
    merged["as_of"] = str(closes.index[-1].date()) if closes is not None and not closes.empty else None
    merged["n_obs"] = int(len(portfolio_returns)) if portfolio_returns is not None else 0
    # Raw checker measurements — surfaced to the dashboard for detail rows
    merged["levels"] = levels
    return merged