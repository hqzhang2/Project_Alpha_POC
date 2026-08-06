#!/usr/bin/env python3
"""
NS-5 Efficient Frontier — the theoretical spine of the grading engine.

Computes a long-only mean-variance efficient frontier for the portfolio's
own asset universe, plus the current portfolio's and the policy's positions
on the return-volatility plane.

Method (per research doc §2/§3.4 — frontier methodology, do not change):
- Covariance: Ledoit-Wolf shrinkage (sklearn) — house standard for N ≤ 50
- Frontier points: for a grid of target annualized returns between GMV and
  max-single-asset return, minimize w'Σw s.t. w'μ = target, Σw = 1, w ≥ 0
  (SLSQP, deterministic)
- Returns: annualized from daily log-return means
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

import config
import data_fetcher


def _cov_shrunk(returns: pd.DataFrame) -> np.ndarray:
    """Ledoit-Wolf shrunk annualized covariance of daily returns."""
    from sklearn.covariance import LedoitWolf
    lw = LedoitWolf().fit(returns.to_numpy())
    cov_daily = lw.covariance_
    return cov_daily * 252.0


def _portfolio_stats(weights: np.ndarray, mu: np.ndarray, cov: np.ndarray):
    ret = float(weights @ mu)
    vol = float(np.sqrt(weights @ cov @ weights))
    return ret, vol


def compute_frontier(closes: pd.DataFrame,
                     tickers: List[str],
                     n_points: int = 40) -> Dict:
    """
    Compute the efficient frontier for the given universe (long-only).

    Args:
        closes:   DataFrame of daily closes (index=date, cols=tickers)
        tickers:  universe to optimize over
        n_points: number of frontier points

    Returns:
        dict: {tickers, mu: {tk: ann_ret}, sigma: {tk: ann_vol},
               frontier: [{vol, ret}], gmv: {vol, ret},
               max_ret: {tk, ret, vol}}
    """
    available = [t for t in tickers if t in closes.columns]
    if len(available) < 2:
        return {"error": "need at least 2 tickers with price data",
                "available": available}

    rets = data_fetcher.compute_log_returns(closes[available].copy())
    if rets.empty or len(rets) < 60:
        return {"error": f"insufficient return data ({len(rets)} rows)"}

    mu_daily = rets.mean().to_numpy()
    mu = mu_daily * 252.0
    cov = _cov_shrunk(rets)

    # Portfolio variance for single-asset positions (for max-ret anchor)
    single_vol = np.sqrt(np.diag(cov))
    max_idx = int(np.argmax(mu))

    # Global minimum variance (GMV) — closed form unconstrained, then clip ≥0
    n = len(available)
    inv_cov = np.linalg.inv(cov)
    ones = np.ones(n)
    w_gmv = inv_cov @ ones / (ones @ inv_cov @ ones)
    w_gmv = np.clip(w_gmv, 0, 1)
    if w_gmv.sum() > 0:
        w_gmv = w_gmv / w_gmv.sum()
    gmv_ret, gmv_vol = _portfolio_stats(w_gmv, mu, cov)

    # Frontier: grid target returns from GMV to max single-asset return
    r_min = gmv_ret
    r_max = float(mu[max_idx])
    if r_max <= r_min + 1e-6:
        r_max = r_min + 1e-4  # degenerate universe — flat frontier
    targets = np.linspace(r_min, r_max, n_points)

    from scipy.optimize import minimize

    frontier = []
    for target in targets:
        # Minimize 0.5 w'Σw s.t. w'μ = target, Σw = 1, 0 ≤ w ≤ 1
        def obj(w):
            return 0.5 * w @ cov @ w

        cons = (
            {"type": "eq", "fun": lambda w: w @ mu - target},
            {"type": "eq", "fun": lambda w: w.sum() - 1.0},
        )
        bounds = [(0.0, 1.0)] * n
        w0 = np.full(n, 1.0 / n)
        res = minimize(obj, w0, method="SLSQP", bounds=bounds,
                       constraints=cons, options={"ftol": 1e-10, "maxiter": 500})
        if not res.success:
            continue
        ret, vol = _portfolio_stats(res.x, mu, cov)
        frontier.append({"vol": round(vol, 4), "ret": round(ret, 4)})

    if len(frontier) < 3:
        return {"error": "frontier optimization failed — universe may be "
                         "numerically degenerate", "available": available}

    # Sort by vol ascending (frontier curve left→right)
    frontier.sort(key=lambda p: p["vol"])

    return {
        "tickers": available,
        "mu": {t: round(float(m), 4) for t, m in zip(available, mu)},
        "sigma": {t: round(float(v), 4) for t, v in zip(available, single_vol)},
        "frontier": frontier,
        "gmv": {"vol": round(gmv_vol, 4), "ret": round(gmv_ret, 4)},
        "max_ret": {"ticker": available[max_idx], "ret": round(r_max, 4),
                    "vol": round(float(single_vol[max_idx]), 4)},
    }


def position_on_frontier(holdings: Dict[str, float],
                         closes: pd.DataFrame,
                         tickers: List[str]) -> Dict:
    """
    Compute a portfolio's (ret, vol) position on the plane, using the same
    Ledoit-Wolf covariance as the frontier (consistent risk measure).

    Returns: {ret, vol, tickers: [...]} or {"error": ...}
    """
    available = [t for t in tickers if t in closes.columns]
    if not available:
        return {"error": "no tickers with price data"}
    rets = data_fetcher.compute_log_returns(closes[available].copy())
    if rets.empty or len(rets) < 60:
        return {"error": "insufficient return data"}

    mu = rets.mean().to_numpy() * 252.0
    cov = _cov_shrunk(rets)

    weights = np.array([holdings.get(t, 0.0) for t in available])
    s = weights.sum()
    if s <= 0:
        return {"error": "holdings sum to zero"}
    weights = weights / s

    ret, vol = _portfolio_stats(weights, mu, cov)
    return {"ret": round(ret, 4), "vol": round(vol, 4), "tickers": available,
            "n_obs": int(len(rets))}