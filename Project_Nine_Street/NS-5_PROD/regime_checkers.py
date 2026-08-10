#!/usr/bin/env python3
"""
NS-5 Regime Axis — checker functions (cheap-model work, Phase 2b).

Deterministic math only. Methodology (grade bounds, merge weights, tweak
language, enhancer formula) lives in regime.py — do NOT change it.

Four checkers feeding regime.py's grade functions:

  check_frontier_shift  → {gmv_all: {tk: w}, gmv_current: {tk: w}}
  check_tangency        → {sharpe_all: float, sharpe_current: float}
  check_policy_gap      → {policy_weights: {tk: w}, current_tangency_weights: {tk: w}}
  check_corr_structure  → {stock_bond_corr: float}

Design:
  - GMV weights: closed-form w = inv(Σ)1 / (1'inv(Σ)1), clip ≥ 0, normalize
    (same math frontier.py uses internally — Ledoit-Wolf via _cov_shrunk)
  - Tangency weights: closed-form w = inv(Σ)μ / (1'inv(Σ)μ), clip ≥ 0, normalize
    (same approach as drift.py _tangency_weights)
  - All fail-open: insufficient data → empty dict / None → grade N/A
  - No network in checkers themselves; regime history + closes are inputs
    (run_regime_checkers orchestrates fetching)

Module: Project_Nine_Street/NS-5_QA/regime_checkers.py
Author: Junior LLM (cheap model), 2026-08-09
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd

import data_fetcher
import frontier as frontier_mod
import theta as theta_mod

MIN_OBS = 60  # minimum return rows for covariance estimation


# =============================================================================
# Closed-form helpers (deterministic math)
# =============================================================================

def _cov(rets: pd.DataFrame) -> np.ndarray:
    """Ledoit-Wolf shrunk annualized covariance (house standard)."""
    return frontier_mod._cov_shrunk(rets)


def _gmv_weights(rets: pd.DataFrame) -> Dict[str, float]:
    """Global minimum-variance weights: inv(Σ)1 / (1'inv(Σ)1), clip ≥ 0.

    Fail-open: {} on insufficient data or singular covariance.
    """
    if rets is None or len(rets) < MIN_OBS or rets.shape[1] < 2:
        return {}
    try:
        cov = _cov(rets)
        n = len(cov)
        inv = np.linalg.pinv(cov)
        ones = np.ones(n)
        w = inv @ ones
        denom = ones @ w
        if abs(denom) < 1e-12:
            return {}
        w = w / denom
        w = np.clip(w, 0, 1)          # long-only flavor for comparison
        if w.sum() > 0:
            w = w / w.sum()
        return {str(tk): round(float(v), 4) for tk, v in zip(rets.columns, w)}
    except Exception:
        return {}


def _tangency_weights(rets: pd.DataFrame) -> Dict[str, float]:
    """Tangency (max-Sharpe) weights: inv(Σ)μ / (1'inv(Σ)μ), clip ≥ 0.

    Fail-open: {} on insufficient data or singular covariance.
    """
    if rets is None or len(rets) < MIN_OBS or rets.shape[1] < 2:
        return {}
    try:
        mu = rets.mean().to_numpy() * 252
        cov = _cov(rets)
        inv = np.linalg.pinv(cov)
        w = inv @ mu
        denom = np.ones(len(mu)) @ w
        if abs(denom) < 1e-12:
            return {}
        w = w / denom
        w = np.clip(w, 0, 1)
        if w.sum() > 0:
            w = w / w.sum()
        return {str(tk): round(float(v), 4) for tk, v in zip(rets.columns, w)}
    except Exception:
        return {}


def _tangency_sharpe(rets: pd.DataFrame) -> Optional[float]:
    """Annualized Sharpe of the tangency portfolio. Fail-open: None."""
    w = _tangency_weights(rets)
    if not w:
        return None
    try:
        mu = rets.mean().to_numpy() * 252
        cov = _cov(rets)
        ws = np.array([w[t] for t in rets.columns])
        ret = float(ws @ mu)
        vol = float(np.sqrt(ws @ cov @ ws))
        return (ret / vol) if vol > 0 else None
    except Exception:
        return None


# =============================================================================
# C1–C4 — Checkers
# =============================================================================

def check_frontier_shift(rets_all: pd.DataFrame,
                         rets_current: pd.DataFrame,
                         theta: dict = None) -> dict:
    """GMV weight mix: all-regime vs current-regime.

    Returns {gmv_all: {tk: w}, gmv_current: {tk: w}} — the grade function
    computes the Euclidean distance between the two mixes.
    Fail-open: {} on either side → grade N/A.
    """
    return {
        "gmv_all": _gmv_weights(rets_all) if rets_all is not None else {},
        "gmv_current": _gmv_weights(rets_current) if rets_current is not None else {},
    }


def check_tangency(rets_all: pd.DataFrame,
                   rets_current: pd.DataFrame,
                   theta: dict = None) -> dict:
    """Tangency Sharpe: all-regime vs current-regime.

    Returns {sharpe_all, sharpe_current} — the grade function computes the
    ratio (degradation). Fail-open: None on either side → grade N/A.
    """
    return {
        "sharpe_all": _tangency_sharpe(rets_all) if rets_all is not None else None,
        "sharpe_current": _tangency_sharpe(rets_current) if rets_current is not None else None,
    }


def check_policy_gap(policy_weights: Dict[str, float],
                     rets_current: pd.DataFrame,
                     theta: dict = None) -> dict:
    """Policy allocation vs current-regime tangency weights.

    Returns {policy_weights: {tk: w}, current_tangency_weights: {tk: w}} —
    the grade function computes the Euclidean distance.
    Fail-open: empty policy or tangency → grade N/A.
    """
    policy = {}
    if policy_weights:
        try:
            policy = {str(k): float(v) for k, v in policy_weights.items() if v}
        except (TypeError, ValueError):
            policy = {}
    return {
        "policy_weights": policy,
        "current_tangency_weights": (
            _tangency_weights(rets_current) if rets_current is not None else {}),
    }


def check_corr_structure(closes: pd.DataFrame,
                         theta: dict = None) -> dict:
    """Stock-bond 60d rolling correlation (NYSE trading days).

    Uses SPY/TLT if present in the closes universe, else falls back to
    QQQ (equity proxy) + TLT. Fail-open: missing pair → None → grade N/A.

    Returns {stock_bond_corr: float | None}
    """
    if closes is None or closes.empty:
        return {"stock_bond_corr": None}
    eq = "SPY" if "SPY" in closes.columns else ("QQQ" if "QQQ" in closes.columns else None)
    bd = "TLT" if "TLT" in closes.columns else None
    if not eq or not bd:
        return {"stock_bond_corr": None}
    try:
        rets = data_fetcher.compute_log_returns(closes[[eq, bd]].dropna())
        if rets is None or len(rets) < 61:
            return {"stock_bond_corr": None}
        corr = float(rets.iloc[:, 0].rolling(60).corr(rets.iloc[:, 1]).iloc[-1])
        if np.isnan(corr):
            return {"stock_bond_corr": None}
        return {"stock_bond_corr": round(corr, 4)}
    except Exception:
        return {"stock_bond_corr": None}


# =============================================================================
# C7 — Regime pipeline: history → returns → checkers → grade
# =============================================================================

def _get_regime_history(theta: dict, force_refresh: bool = False) -> pd.DataFrame:
    """Regime labels from the SQLite store, else a fresh pipeline run.

    Fail-open: empty DataFrame on any failure (no network in the grade path
    unless the store is empty).
    """
    from common.regime_store import query_window
    days = theta.get("regime", {}).get("regime_history_days", 750)
    try:
        history = query_window(days=days)
        if not history.empty and not force_refresh:
            return history
    except Exception:
        history = pd.DataFrame()
    try:
        from common.regime_pipeline import run_regime_pipeline
        history = run_regime_pipeline(days_back=days)
    except Exception:
        pass  # fail-open: return what we have
    return history


def run_regime_checkers(closes: Optional[pd.DataFrame] = None,
                        policy_weights: Optional[Dict[str, float]] = None,
                        theta: dict = None,
                        force_refresh: bool = False) -> dict:
    """End-to-end regime axis: fetch history → split returns by regime →
    4 checkers → run_regime_grade. Returns the full axis result dict.

    Args:
        closes:         daily closes DataFrame (portfolio + policy universe)
        policy_weights: policy target weights {ticker: weight}
        theta:          owner parameter vector (load_theta())
        force_refresh:  bypass the regime-history store cache

    Returns:
        run_regime_grade() result: {sub_grades, composite_regime_grade,
        composite_regime_score, severity, enhancer, tweaks, levels}
        Fail-open: {'composite_regime_grade': 'N/A', ...} + error key.
    """
    import regime as regime_mod

    if theta is None:
        theta = theta_mod.load_theta()

    if theta.get("regime") is None:
        return {
            "composite_regime_grade": "N/A",
            "composite_regime_score": 0.0,
            "severity": "green",
            "error": "regime axis disabled — configure Θ.regime",
        }

    # 1. Regime history (store → pipeline)
    history = _get_regime_history(theta, force_refresh=force_refresh)
    if history.empty or "regime" not in history.columns:
        return {
            "composite_regime_grade": "N/A",
            "composite_regime_score": 0.0,
            "severity": "green",
            "error": "no regime history available — run regime pipeline or set FRED_API_KEY",
        }
    current_regime = str(history["regime"].iloc[-1])

    # 2. Returns (all-regime vs current-regime filtered)
    if closes is None or closes.empty:
        return {
            "composite_regime_grade": "N/A",
            "composite_regime_score": 0.0,
            "severity": "green",
            "error": "no closes data",
        }
    rets_all = data_fetcher.compute_log_returns(closes)
    if rets_all is None or rets_all.empty or len(rets_all) < MIN_OBS:
        return {
            "composite_regime_grade": "N/A",
            "composite_regime_score": 0.0,
            "severity": "green",
            "error": f"insufficient return data ({0 if rets_all is None else len(rets_all)} rows)",
        }

    from common.regime_model import filter_regime_returns
    rets_current = filter_regime_returns(
        rets_all, history, current_regime, min_days=MIN_OBS)
    if rets_current.empty:
        rets_current = rets_all  # fail-open: current regime too sparse → all-regime

    # 3. Checkers
    fs = check_frontier_shift(rets_all, rets_current, theta)
    td = check_tangency(rets_all, rets_current, theta)
    pg = check_policy_gap(policy_weights or theta.get("policy_weights", {}),
                          rets_current, theta)
    cs = check_corr_structure(closes, theta)

    # 4. Grade pipeline (frontier-owned methodology)
    return regime_mod.run_regime_grade(fs, td, pg, cs, current_regime, theta)
