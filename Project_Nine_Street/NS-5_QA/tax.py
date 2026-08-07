#!/usr/bin/env python3
"""
NS-5 Tax Axis — grade functions, composite merger, and tweak language.
Money-path: methodology, thresholds, and language (frontier-owned).
Checker functions (deterministic math) are cheap-model work (C1-C5).
"""

from __future__ import annotations

from typing import Dict, List, Optional

import theta as theta_mod


# =============================================================================
# Helpers — classification + drag computation
# =============================================================================

def _classify_distribution(ticker: str, theta: dict) -> Dict:
    """Resolve distribution character for a ticker.

    Priority: theta.distribution_character table → heuristic → default.
    Returns {"character": "qualified"|"ordinary"|"roc"|"sec1256", "annual_roc_rate": 0, "unclassified": bool}
    """
    tax = theta.get("tax", {})
    if not tax:
        return {"character": "qualified", "annual_roc_rate": 0, "unclassified": True}

    char_table = tax.get("distribution_character", {})
    info = char_table.get(ticker)
    if info:
        result = {"character": info.get("character", "qualified"), "annual_roc_rate": info.get("annual_roc_rate", 0), "unclassified": False}
        if result["character"] == "roc" and not result["annual_roc_rate"]:
            result["annual_roc_rate"] = 0.10  # default 10%/yr ROC if not specified
        return result

    # Heuristic: sector_map lookup
    sector_map = theta.get("sector_map", {})
    sector = sector_map.get(ticker, "Unknown")
    if sector.startswith("Fixed-Income"):
        result = {"character": "ordinary", "annual_roc_rate": 0, "unclassified": True}
    else:
        result = {"character": "qualified", "annual_roc_rate": 0, "unclassified": True}
    return result


def _compute_drags(theta: dict) -> Dict[str, float]:
    """Single source of truth: compute drag rates from bracket + NIIT + state."""
    tax = theta.get("tax", {})
    if not tax:
        return {"ordinary": 0, "ltcg": 0, "blended_1256": 0, "roc": 0}
    fb = tax.get("federal_bracket", 0.37)
    lg = tax.get("ltcg_rate", 0.20)
    niit_surcharge = 0.038 if tax.get("niit", True) else 0
    sr = tax.get("state_rate", 0.0)
    ordinary_drag = fb + niit_surcharge + sr
    ltcg_drag = lg + niit_surcharge + sr
    blended_1256 = 0.60 * ltcg_drag + 0.40 * ordinary_drag
    roc_drag = 0.0
    return {"ordinary": ordinary_drag, "ltcg": ltcg_drag, "blended_1256": blended_1256, "roc": roc_drag}


# =============================================================================
# F2 — Grade functions (score → letter + severity)
# =============================================================================

def _score_to_letter(score: float, bounds: list) -> str:
    """Map score to letter grade via descending thresholds."""
    for threshold, letter in bounds:
        if score >= threshold:
            return letter
    return "F"


def _severity(score: float, theta: dict, key: str = "tax_severity_bounds") -> str:
    """Score → severity (green/yellow/orange/red)."""
    tax = theta.get("tax", {})
    bounds = tax.get(key, [(5.0, "green"), (3.5, "yellow"), (2.0, "orange"), (0, "red")])
    for threshold, sev in bounds:
        if score >= threshold:
            return sev
    return "red"


def grade_after_tax_gap(pre_tax_sharpe: Optional[float],
                        after_tax_sharpe: Optional[float],
                        substitution_exists: bool,
                        theta: dict) -> Dict:
    """
    Grade the after-tax frontier impact.
    gap = pre-tax Sharpe − after-tax Sharpe at the portfolio's risk point.
    A: ≤0.5pp gap (negligible) → F: >3pp gap (severe tax drag).
    """
    if pre_tax_sharpe is None or after_tax_sharpe is None:
        return {"composite_grade": "N/A", "composite_score": 0,
                "severity": "green", "gap_pp": None, "substitution_available": False}

    gap_pp = abs(pre_tax_sharpe - after_tax_sharpe)
    # Score: 5.0 for no gap, linear down to 0 for ≥10pp gap, clamped
    score = max(0, min(5, 5.0 - (gap_pp / 3.0) * 5.0))
    score = round(score, 2)
    letter = _score_to_letter(score, [(4.5,"A"),(3.5,"B"),(2.5,"C"),(1.5,"D"),(0,"F")])
    sev = _severity(score, theta)
    substitution_available = substitution_exists or gap_pp > 1.0

    return {"composite_grade": letter, "composite_score": score,
            "severity": sev, "gap_pp": round(gap_pp, 2),
            "substitution_available": substitution_available,
            "pre_tax_sharpe": round(pre_tax_sharpe, 4) if pre_tax_sharpe is not None else None,
            "after_tax_sharpe": round(after_tax_sharpe, 4) if after_tax_sharpe is not None else None}


def grade_tlh(harvestable_pool_ratio: float,
              largest_savings_bp: float,
              harvest_candidates: int,
              theta: dict) -> Dict:
    """
    Grade TLH opportunity.
    harvestable_pool_ratio = loss pool / portfolio value.
    A: <0.5% (negligible) → F: >10% (large opportunity unrealized).
    """
    # Score: 5.0 for 0 harvestable, drops to 1.0 for 20%+ harvestable
    score = max(1.0, 5.0 - (harvestable_pool_ratio / 0.05))
    score = round(score, 2)
    letter = _score_to_letter(score, [(4.5,"A"),(3.5,"B"),(2.5,"C"),(1.5,"D"),(0,"F")])
    sev = _severity(score, theta)

    return {"composite_grade": letter, "composite_score": score,
            "severity": sev, "harvestable_pool_ratio": round(harvestable_pool_ratio, 4),
            "largest_savings_bp": round(largest_savings_bp, 1),
            "harvest_candidates": harvest_candidates}


def grade_asset_location(mismatch_count: int,
                         total_positions: int,
                         max_drag_gap_bp: float,
                         theta: dict) -> Dict:
    """
    Grade asset location efficiency.
    mismatch_count = positions in suboptimal account type.
    A: 0% → F: >50%.
    """
    ratio = mismatch_count / max(total_positions, 1)
    score = max(1.0, 5.0 - ratio * 8.0)  # 50%+ = F
    score = round(score, 2)
    letter = _score_to_letter(score, [(4.5,"A"),(3.5,"B"),(2.5,"C"),(1.5,"D"),(0,"F")])
    sev = _severity(score, theta)

    return {"composite_grade": letter, "composite_score": score,
            "severity": sev, "mismatch_count": mismatch_count,
            "total_positions": total_positions, "mismatch_ratio": round(ratio, 3),
            "max_drag_gap_bp": round(max_drag_gap_bp, 1)}


def grade_basis_erosion(max_erosion_ratio: float,
                        locked_positions: int,
                        near_locked_positions: int,
                        theta: dict) -> Dict:
    """
    Grade basis erosion severity.
    max_erosion_ratio: worst erosion % across all positions.
    locked: erosion > 90% (position effectively stuck).
    A: 0% → F: >90%.
    """
    tax = theta.get("tax", {})
    bounds = tax.get("frontier_gap_bounds", [(0.5,"A"),(1.0,"B"),(2.0,"C"),(3.0,"D"),(999,"F")])

    # Score based on erosion ratio — logarithmic toward 90%
    score = max(1.0, 5.0 - max_erosion_ratio * 4.5)
    score = round(score, 2)
    letter = _score_to_letter(score, [(4.5,"A"),(3.5,"B"),(2.5,"C"),(1.5,"D"),(0,"F")])
    sev = _severity(score, theta)

    return {"composite_grade": letter, "composite_score": score,
            "severity": sev, "max_erosion_ratio": round(max_erosion_ratio, 3),
            "locked_positions": locked_positions, "near_locked_positions": near_locked_positions}


# =============================================================================
# F3 — Composite merger + tweak language
# =============================================================================

def merge_tax_grade(levels: Dict[str, Dict], theta: dict) -> Dict:
    """
    Weighted composite of the 4 tax sub-grades.

    levels keys: after_tax, tlh, location, erosion (each a grade dict from the
    corresponding grade_* function). Missing keys get zero weight (fail-open).
    """
    tax = theta.get("tax", {})
    weights = tax.get("tax_axis_weights", {
        "after_tax": 0.30,
        "tlh":       0.20,
        "location":  0.30,
        "erosion":   0.20,
    })

    numerator = 0.0
    denominator = 0.0
    sub_grades = {}

    for key in ("after_tax", "tlh", "location", "erosion"):
        g = levels.get(key)
        if g is None or g.get("composite_score") is None or g.get("composite_score") == 0:
            # fail-open: missing axis gets zero weight, doesn't drag composite
            sub_grades[key] = {"grade": "N/A", "score": 0, "weight": weights.get(key, 0)}
            continue
        w = weights.get(key, 0)
        sc = g.get("composite_score", 0)
        numerator += sc * w
        denominator += w
        sub_grades[key] = {"grade": g.get("composite_grade", "N/A"),
                           "score": sc,
                           "weight": w,
                           "severity": g.get("severity", "green")}

    composite_score = round(numerator / max(denominator, 0.01), 2)
    composite_grade = _score_to_letter(composite_score, [(4.5,"A"),(3.5,"B"),(2.5,"C"),(1.5,"D"),(0,"F")])
    severity = _severity(composite_score, theta)

    return {"composite_tax_grade": composite_grade,
            "composite_tax_score": composite_score,
            "severity": severity,
            "sub_grades": sub_grades}


def generate_tax_tweaks(levels: Dict[str, Dict], theta: dict) -> List[Dict]:
    """
    Ranked tweaks: after-tax gap → TLH → location → erosion.
    Each tweak: {axis, sub_axis, severity, recommended_action, rationale}
    """
    tweaks = []

    # --- After-tax gap tweaks ---
    at = levels.get("after_tax", {})
    if at.get("composite_score") and at.get("composite_score") < 4.5:
        gap = at.get("gap_pp", 0)
        sev = "critical" if gap > 3 else ("high" if gap > 2 else ("medium" if gap > 0.5 else "medium"))
        tweaks.append({
            "axis": "tax",
            "sub_axis": "after_tax_frontier",
            "severity": sev,
            "recommended_action":
                f"after-tax Sharpe gap {gap}pp: pre-tax {at.get('pre_tax_sharpe','?')} → "
                f"{at.get('after_tax_sharpe','?')} after-market tax drag"
                + (" — substitution available" if at.get("substitution_available") else ""),
            "rationale": "tax drag reduces risk-adjusted return; tax-efficient alternatives may exist",
        })

    # --- TLH tweaks ---
    tlh = levels.get("tlh", {})
    if tlh.get("composite_score") and tlh.get("composite_score") < 4.5:
        pool = tlh.get("harvestable_pool_ratio", 0) * 100
        sev = "critical" if pool > 10 else ("high" if pool > 5 else ("medium" if pool > 1 else "medium"))
        tweaks.append({
            "axis": "tax",
            "sub_axis": "tlh",
            "severity": sev,
            "recommended_action":
                f"harvest {pool:.1f}% harvestable pool: {tlh.get('harvest_candidates', 0)} lots, "
                f"largest savings {tlh.get('largest_savings_bp', 0):.0f} bp — apply against current-year gains or $3K ordinary income",
            "rationale": "unrealized losses can offset realized gains; ST > LT preference",
        })

    # --- Asset location tweaks ---
    loc = levels.get("location", {})
    if loc.get("composite_score") and loc.get("composite_score") < 4.5:
        m = loc.get("mismatch_count", 0)
        sev = "critical" if m >= 3 else ("high" if m >= 2 else "medium")
        tweaks.append({
            "axis": "tax",
            "sub_axis": "asset_location",
            "severity": sev,
            "recommended_action":
                f"{m}/{loc.get('total_positions','?')} positions in suboptimal account — "
                f"largest drag gap {loc.get('max_drag_gap_bp',0):.0f} bp",
            "rationale": "ordinary-income funds in taxable lose 3–4pp/yr vs tax-advantaged",
        })

    # --- Basis erosion tweaks ---
    ero = levels.get("erosion", {})
    if ero.get("composite_score") and ero.get("composite_score") < 4.5:
        max_e = ero.get("max_erosion_ratio", 0) * 100
        locked = ero.get("locked_positions", 0)
        sev = "critical" if max_e > 90 else ("high" if max_e > 75 else ("medium" if max_e > 50 else "medium"))
        tweaks.append({
            "axis": "tax",
            "sub_axis": "basis_erosion",
            "severity": sev,
            "recommended_action":
                f"basis erosion {max_e:.0f}% — {locked} position(s) locked at >90% erosion; "
                "consider partial exit before position becomes unstuck",
            "rationale": "ROC funds silently erode basis — at 90% erosion any sale has a large LTCG bill",
        })

    return tweaks
