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


# =============================================================================
# C3-C5 — Checker functions (deterministic math, cheap-model work)
# Input: v2 positions {ticker: {shares, account, lots}} + yields + current prices
# Output: dicts consumable by the grade_* functions + generate_tax_tweaks
# =============================================================================

def check_tlh_harvest(positions, current_prices, theta: dict) -> Dict:
    """
    C3 — TLH harvest candidates.
    Per lot: unrealized_pnl = (current_price - cost_per_share) x lot_shares.
    Loss lots are candidates; holding period (365d) -> ST vs LT rate.
    Wash-sale v1: flag when a same-ticker lot was bought within +/-window of
    the loss lot date (flag-all-pairs for PM review — no identity classifier).
    """
    tax = theta.get("tax", {})
    if not tax:
        return {"composite_grade": "N/A", "composite_score": 0, "severity": "green",
                "harvestable_pool_ratio": 0.0, "largest_savings_bp": 0.0,
                "harvest_candidates": 0, "items": [], "wash_sale_flags": 0}
    from datetime import date
    window = tax.get("wash_sale_window_days", 30)
    ordinary = tax.get("ordinary_drag", 0.408)
    ltcg = tax.get("ltcg_drag", 0.238)

    today = date.today()
    items = []
    pool = 0.0
    total_value = 0.0
    wash_flags = 0

    for tk, pos in positions.items():
        if not isinstance(pos, dict) or pos.get("account", "taxable") != "taxable":
            continue  # TLH only in taxable accounts
        lots = pos.get("lots") or []
        price = current_prices.get(tk, 0.0) or 0.0
        shares_total = float(pos.get("shares", 0))
        total_value += price * shares_total
        lot_dates = []
        for lot in lots:
            lot_date = lot.get("date")
            lot_shares = float(lot.get("shares", 0))
            cost = float(lot.get("cost_per_share", 0))
            if not lot_date or lot_shares <= 0 or cost <= 0:
                continue  # unknown lot -> skip (fail-open)
            try:
                d = date.fromisoformat(str(lot_date))
            except ValueError:
                continue
            lot_dates.append((d, tk))
            pnl = (price - cost) * lot_shares
            if pnl >= 0:
                continue
            holding_days = (today - d).days
            period = "LT" if holding_days > 365 else "ST"
            rate = ltcg if period == "LT" else ordinary
            savings = abs(pnl) * rate
            pool += abs(pnl)
            items.append({
                "ticker": tk, "lot_date": lot_date, "shares": lot_shares,
                "unrealized_pnl": round(pnl, 2), "holding_period": period,
                "est_savings": round(savings, 2),
            })
        # Wash-sale v1: any two same-ticker lots within +/-window
        for i in range(len(lot_dates)):
            for j in range(i + 1, len(lot_dates)):
                if lot_dates[i][1] != lot_dates[j][1]:
                    continue
                gap = abs((lot_dates[i][0] - lot_dates[j][0]).days)
                if gap <= window:
                    wash_flags += 1

    pool_ratio = pool / total_value if total_value > 0 else 0.0
    largest = max((i["est_savings"] for i in items), default=0.0)
    # largest savings as basis points of portfolio value
    largest_bp = largest / total_value * 10000 if total_value > 0 else 0.0

    result = grade_tlh(pool_ratio, largest_bp, len(items), theta)
    result["items"] = items
    result["wash_sale_flags"] = wash_flags
    return result


def check_asset_location(positions, distribution_char, theta: dict) -> Dict:
    """
    C4 — Asset location checker.
    For each position: is its account type optimal for its distribution character?
    ordinary -> ira/401k (defer); qualified -> roth/taxable (LTCG);
    roc -> taxable (0% current); sec1256 -> taxable.
    Mismatch: ordinary in taxable; qualified in ira/401k (withdrawal ordinary).
    """
    tax = theta.get("tax", {})
    if not tax:
        return {"composite_grade": "N/A", "composite_score": 0, "severity": "green",
                "mismatch_count": 0, "total_positions": 0, "mismatch_ratio": 0.0,
                "max_drag_gap_bp": 0.0, "items": []}
    treatment = tax.get("account_treatment", {})
    ordinary = tax.get("ordinary_drag", 0.408)
    ltcg = tax.get("ltcg_drag", 0.238)

    total = 0
    mismatches = 0
    max_gap_bp = 0.0
    items = []

    for tk, pos in positions.items():
        if not isinstance(pos, dict):
            continue
        account = pos.get("account", "taxable")
        total += 1
        char_info = distribution_char.get(tk, {})
        char = char_info.get("character", "qualified") if isinstance(char_info, dict) else "qualified"

        if char == "ordinary":
            # Best in ira/401k (defer 40.8%); taxable = 40.8% drag
            drag = ordinary if account == "taxable" else 0.0
            mismatch = account == "taxable"
            rec = "ira/401k"
        elif char == "sec1256":
            drag = tax.get("blended_1256_drag", 0.28) if account == "taxable" else 0.0
            mismatch = account == "taxable"
            rec = "ira/401k"
        elif char == "roc":
            drag = 0.0  # location-neutral (0 current tax)
            mismatch = False
            rec = account
        else:  # qualified
            # Best roth/taxable; ira/401k withdrawal = ordinary (40.8%)
            if account in ("ira", "401k"):
                drag = ordinary
                mismatch = True
                rec = "taxable/roth"
            else:
                drag = 0.0
                mismatch = False
                rec = account

        gap_bp = drag * 10000 if drag > 0 else 0.0
        max_gap_bp = max(max_gap_bp, gap_bp)
        if mismatch:
            mismatches += 1
        items.append({
            "ticker": tk, "account": account, "character": char,
            "drag_if_current": round(drag, 4), "mismatch": mismatch,
            "recommended_account": rec,
        })

    result = grade_asset_location(mismatches, total, max_gap_bp, theta)
    result["items"] = items
    return result


def check_basis_erosion(positions, distribution_char, theta: dict) -> Dict:
    """
    C5 — Basis erosion tracker (ROC funds).
    erosion_ratio = min(1, annual_roc_rate x years_held) — static annualized
    ROC assumption (v1; 19a ingestion future). Thresholds: 50/75/90%.
    """
    tax = theta.get("tax", {})
    if not tax:
        return {"composite_grade": "N/A", "composite_score": 0, "severity": "green",
                "max_erosion_ratio": 0.0, "locked_positions": 0,
                "near_locked_positions": 0, "items": []}
    from datetime import date
    thresholds = tax.get("erosion_thresholds", [0.50, 0.75, 0.90])
    today = date.today()

    max_erosion = 0.0
    locked = 0
    near_locked = 0
    items = []

    for tk, pos in positions.items():
        if not isinstance(pos, dict):
            continue
        char_info = distribution_char.get(tk, {})
        char = char_info.get("character", "qualified") if isinstance(char_info, dict) else "qualified"
        if char != "roc":
            continue
        roc_rate = char_info.get("annual_roc_rate", 0.10) if isinstance(char_info, dict) else 0.10
        for lot in pos.get("lots") or []:
            lot_date = lot.get("date")
            if not lot_date:
                continue
            try:
                d = date.fromisoformat(str(lot_date))
            except ValueError:
                continue
            years = max(0.0, (today - d).days / 365.0)
            erosion = min(1.0, roc_rate * years)
            max_erosion = max(max_erosion, erosion)
            if erosion >= 0.90:
                locked += 1
            elif erosion >= 0.75:
                near_locked += 1
            items.append({
                "ticker": tk, "lot_date": lot_date, "years_held": round(years, 1),
                "annual_roc_rate": roc_rate, "erosion_ratio": round(erosion, 3),
                "erosion_pct": round(erosion * 100, 0),
                "warning_level": max((t for t in thresholds if erosion >= t), default=None),
            })

    result = grade_basis_erosion(max_erosion, locked, near_locked, theta)
    result["items"] = items
    return result


# =============================================================================
# C7 — After-tax frontier checker + run_tax_grade orchestrator
# =============================================================================

def check_after_tax_frontier(portfolio_returns, positions, yields, theta: dict) -> Dict:
    """
    After-tax frontier impact checker.

    pre-tax Sharpe: annualized mean/std of portfolio returns.
    Portfolio drag: sum over positions of weight_i x yield_i x rate(char, account).
    After-tax Sharpe: (pre-tax mean - drag) / std — constant shift, Sigma unchanged.
    substitution_available: portfolio drag > 1pp -> tax-efficient alternatives exist.
    """
    tax = theta.get("tax", {})
    if not tax or portfolio_returns is None or portfolio_returns.empty:
        return {"composite_grade": "N/A", "composite_score": 0, "severity": "green",
                "gap_pp": None, "substitution_available": False, "items": []}
    import numpy as np

    drags = _compute_drags(theta)
    treatment = tax.get("account_treatment", {})
    chars = {tk: _classify_distribution(tk, theta) for tk in positions}

    ret = portfolio_returns.dropna()
    if len(ret) < 20:
        return {"composite_grade": "N/A", "composite_score": 0, "severity": "green",
                "gap_pp": None, "substitution_available": False, "items": []}

    mean_daily = float(ret.mean())
    std_daily = float(ret.std())
    ann_factor = 252.0
    pre_sharpe = (mean_daily * ann_factor) / (std_daily * np.sqrt(ann_factor)) if std_daily > 0 else 0.0

    # Portfolio weighted drag
    total_value = sum(float(p.get("shares", 0)) * yields.get(tk, 0) for tk, p in positions.items()
                      if isinstance(p, dict)) if yields else 0.0
    # Use yields as a proxy weight when no price available: fall back to uniform
    items = []
    total_drag = 0.0
    pos_items = []
    for tk, p in positions.items():
        if not isinstance(p, dict):
            continue
        char = chars.get(tk, {}).get("character", "qualified")
        account = p.get("account", "taxable")
        at = treatment.get(account, {})
        if not at.get("dividend_drag", True):
            drag = 0.0
        else:
            yld = yields.get(tk, 0.0)
            rate = drags.get(char, drags.get("ordinary", 0.408)) if char in drags else (
                {"qualified": drags["ltcg"], "ordinary": drags["ordinary"],
                 "roc": drags["roc"], "sec1256": drags["blended_1256"]}.get(char, 0.408))
            drag = yld * rate
        total_drag += drag
        pos_items.append({"ticker": tk, "account": account, "character": char,
                          "yield": yld if "yld" in locals() else yields.get(tk, 0.0),
                          "drag_annual": round(drag, 4)})

    post_mean = mean_daily * ann_factor - total_drag
    post_sharpe = post_mean / (std_daily * np.sqrt(ann_factor)) if std_daily > 0 else 0.0

    gap = abs(pre_sharpe - post_sharpe)
    substitution = total_drag > 0.01  # >1pp drag = alternatives worth exploring

    result = grade_after_tax_gap(pre_sharpe, post_sharpe, substitution, theta)
    result["items"] = pos_items
    result["portfolio_drag_annual"] = round(total_drag, 4)
    return result


def run_tax_grade(positions, yields, theta: dict = None) -> Dict:
    """
    End-to-end tax grading: 4 checkers -> merge -> tweaks.

    positions: v2 {ticker: {shares, account, lots}}
    yields:    {ticker: decimal yield}
    theta:     must have tax=TAX_DEFAULTS set (else axis inactive)
    """
    import data_fetcher
    import portfolio as portfolio_mod
    import theta as theta_mod

    if theta is None:
        theta = theta_mod.load_theta(tax=theta_mod.TAX_DEFAULTS)
    tax = theta.get("tax")
    if not tax:
        return {"error": "tax axis disabled — configure Θ.tax"}

    # Current prices for TLH (from closes cache)
    tickers = list(positions.keys())
    closes = data_fetcher.get_closes(tickers)
    prices = {}
    if not closes.empty:
        last = closes.iloc[-1]
        prices = {tk: float(last[tk]) if tk in closes.columns else 0.0 for tk in tickers}

    # Portfolio returns for after-tax Sharpe
    holdings = {tk: float(p["shares"]) for tk, p in positions.items() if isinstance(p, dict)}
    port_returns = portfolio_mod.build_portfolio_returns(holdings, closes=closes)

    chars = {tk: _classify_distribution(tk, theta) for tk in positions}

    levels = {
        "after_tax": check_after_tax_frontier(port_returns, positions, yields, theta),
        "tlh": check_tlh_harvest(positions, prices, theta),
        "location": check_asset_location(positions, chars, theta),
        "erosion": check_basis_erosion(positions, chars, theta),
    }
    merged = merge_tax_grade(levels, theta)
    merged["tweaks"] = generate_tax_tweaks(levels, theta)
    merged["levels"] = levels
    return merged
