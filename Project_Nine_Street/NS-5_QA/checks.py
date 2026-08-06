#!/usr/bin/env python3
"""
NS-5 Non-factor concentration checks — sector weights, effective-N, tail correlation.

Roadmap Phase 3.2–3.4 (mechanical, thresholds in theta.py — frontier-set):
- 3.2 grade_sector_weights(): worst-deviating-sector grading rule
- 3.3 grade_effective_n(): linear from N=1 (F) to N>=floor (A)
- 3.4 grade_tail_correlation(): pairwise corr on worst N% of portfolio days

Guardrails (frontier-set, do not change):
- Worst-of sector grading (not average)
- Thresholds live in theta.THETA_DEFAULTS
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd

import config
import data_fetcher


# ============================================================================
# 3.2 Sector weight checker
# ============================================================================

def map_ticker_to_sector(ticker: str, theta: dict) -> str:
    """Ticker → sector label via theta['sector_map']; unknown → 'Unknown'."""
    return theta.get("sector_map", {}).get(ticker, "Unknown")


def _sector_ratio_grade(ratio: float, bounds) -> tuple:
    """ratio = sector_weight / cap → letter, numeric score (5=A)."""
    scores = {"A": 5, "B": 4, "C": 3, "D": 2, "F": 1}
    for upper, letter in bounds:
        if ratio <= upper:
            return letter, scores[letter]
    return "F", 1


def grade_sector_weights(holdings: Dict[str, float], theta: dict) -> Dict:
    """
    Compute sector weights from portfolio, compare each to max_sector_pct cap.
    WORST-OF grading (frontier-set): the grade is the worst-deviating sector,
    not the average.

    Returns:
        {composite_grade, composite_score, sector_weights: {sector: weight},
         sector_details: {sector: {weight, cap, ratio, grade, score, flagged}},
         unknown_tickers: [...]}
    """
    cap = theta["max_sector_pct"]
    bounds = theta["sector_ratio_bounds"]

    # Aggregate weights by sector
    sector_weights: Dict[str, float] = {}
    unknown = []
    for ticker, weight in holdings.items():
        sector = map_ticker_to_sector(ticker, theta)
        if sector == "Unknown":
            unknown.append(ticker)
        sector_weights[sector] = sector_weights.get(sector, 0.0) + weight

    details = {}
    worst_score = 5
    worst_letter = "A"
    for sector, weight in sector_weights.items():
        ratio = weight / cap
        letter, score = _sector_ratio_grade(ratio, bounds)
        details[sector] = {
            "weight": round(weight, 4),
            "cap": cap,
            "ratio": round(ratio, 3),
            "grade": letter,
            "score": score,
            "flagged": score < 4,  # C or worse
        }
        if score < worst_score:
            worst_score = score
            worst_letter = letter

    return {
        "composite_grade": worst_letter,
        "composite_score": worst_score,
        "sector_weights": {k: round(v, 4) for k, v in sector_weights.items()},
        "sector_details": details,
        "unknown_tickers": unknown,
    }


# ============================================================================
# 3.3 Effective-N checker
# ============================================================================

def grade_effective_n(holdings: Dict[str, float], theta: dict) -> Dict:
    """
    N_eff = 1 / Σw_i². Linear grade: N_eff=1 → F (score 1), N_eff >= floor → A.
    Score = 1 + 4 * min(N_eff / floor, 1.0); letter via letter_score_bounds.

    Returns:
        {composite_grade, composite_score, effective_n, floor}
    """
    floor = theta["effective_n_floor"]
    letter_bounds = theta["letter_score_bounds"]

    if not holdings:
        return {"composite_grade": "F", "composite_score": 1.0,
                "effective_n": 0.0, "floor": floor}

    n_eff = 1.0 / sum(w ** 2 for w in holdings.values())
    # Linear 0→5: N_eff=1 → ~0 (F), N_eff >= floor → 5.0 (A).
    # Round FIRST so the letter check sees the same value the output shows
    # (avoids float-precision boundary: 2.49999… vs 2.5).
    score = round(5.0 * min(n_eff / floor, 1.0), 2)

    letter = "F"
    for threshold, ltr in letter_bounds:
        if score >= threshold:
            letter = ltr
            break

    return {
        "composite_grade": letter,
        "composite_score": round(score, 2),
        "effective_n": round(n_eff, 2),
        "floor": floor,
    }


# ============================================================================
# 3.4 Tail-correlation checker
# ============================================================================

def grade_tail_correlation(holdings: Dict[str, float],
                           theta: dict,
                           closes: Optional[pd.DataFrame] = None,
                           force_refresh: bool = False) -> Dict:
    """
    On the worst `tail_pctile`% of portfolio days, compute pairwise correlation
    of the largest `top_n_for_tail` positions. Flag pairs > tail_corr_threshold.
    Grade: 0 flagged pairs → A, 1 → B, 2+ → C (per theta tail_corr_grade).

    Returns:
        {composite_grade, composite_score, flagged_pairs: [(t1, t2, corr)],
         n_tail_days, positions_checked}
    """
    pctile = theta["tail_pctile"]
    threshold = theta["tail_corr_threshold"]
    top_n = theta["top_n_for_tail"]
    grade_map = list(theta["tail_corr_grade"])

    if not holdings:
        return {"composite_grade": "F", "composite_score": 1.0,
                "flagged_pairs": [], "n_tail_days": 0, "positions_checked": 0}

    # Largest N positions by weight
    top = sorted(holdings.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    top_tickers = [t for t, _ in top]

    # Fetch/align closes
    if closes is None or closes.empty:
        closes = data_fetcher.get_closes(top_tickers, force_refresh=force_refresh)
    available = [t for t in top_tickers if t in closes.columns]
    if not available or closes.empty:
        return {"composite_grade": "F", "composite_score": 1.0,
                "flagged_pairs": [], "n_tail_days": 0,
                "positions_checked": len(available), "error": "no close data"}

    df = closes[available].copy()
    rets = data_fetcher.compute_log_returns(df)
    if rets.empty or len(rets) < 30:
        return {"composite_grade": "F", "composite_score": 1.0,
                "flagged_pairs": [], "n_tail_days": 0,
                "positions_checked": len(available), "error": "insufficient return data"}

    # Portfolio daily return = equal proxy: mean of top-N returns is NOT the
    # portfolio — use weighted returns of ALL holdings for tail-day selection.
    # Fall back to mean of available when other tickers lack data.
    all_tickers = list(holdings.keys())
    if closes is not None:
        avail_all = [t for t in all_tickers if t in closes.columns]
        if len(avail_all) >= len(available):
            all_ret = data_fetcher.compute_log_returns(closes[avail_all].copy())
            if not all_ret.empty:
                wts = np.array([holdings[t] for t in avail_all])
                port_ret = (all_ret[avail_all] @ wts)
                port_ret = port_ret.where(np.isfinite(port_ret)).dropna()
                tail_mask = port_ret.index.isin(rets.index)
                if port_ret[tail_mask].notna().sum() >= 30:
                    # worst N% of portfolio days
                    cutoff = np.percentile(port_ret, pctile)
                    tail_days = port_ret[port_ret <= cutoff].index
                    tail_days = tail_days.intersection(rets.index)
                else:
                    tail_days = rets.index
            else:
                tail_days = rets.index
        else:
            tail_days = rets.index
    else:
        tail_days = rets.index

    tail = rets.loc[tail_days]
    if len(tail) < 10:
        return {"composite_grade": "F", "composite_score": 1.0,
                "flagged_pairs": [], "n_tail_days": int(len(tail)),
                "positions_checked": len(available), "error": "too few tail days"}

    # Pairwise correlation on tail days only
    corr = tail.corr()
    flagged = []
    n = len(available)
    for i in range(n):
        for j in range(i + 1, n):
            c = corr.iloc[i, j]
            if np.isfinite(c) and c > threshold:
                flagged.append((available[i], available[j], round(float(c), 3)))

    count = len(flagged)
    # Default to worst grade; improve as count falls within each bound.
    letter = "C"
    score = 3
    for upper, ltr in grade_map:
        if count <= upper:
            letter = ltr
            score = {"A": 5, "B": 4, "C": 3}.get(ltr, 1)
            break

    return {
        "composite_grade": letter,
        "composite_score": score,
        "flagged_pairs": flagged,
        "n_tail_days": int(len(tail)),
        "positions_checked": len(available),
    }