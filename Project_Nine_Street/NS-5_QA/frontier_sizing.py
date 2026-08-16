#!/usr/bin/env python3
"""frontier_sizing.py — R2: frontier-based sizing of the joint universe.

Replaces the equal-weight-within-sleeve stopgap with weights from NS-5's
validated efficient-frontier machinery (Ledoit-Wolf shrunk covariance, long-only
SLSQP). This is the "where the diversification alpha lives" step: size the joint
universe (NS-7 momentum ∪ A_T value) by return/vol/correlation instead of
averaging.

Reuses frontier.py's methodology (do NOT change that module's math):
  - _cov_shrunk / _portfolio_stats  (same risk measure as the frontier)
  - compute_frontier  (the curve — used to pick the sizing point)

This module ADDS what frontier.py does not expose: the actual weight VECTOR at
a chosen point (frontier.py returns the curve, not weights). We solve long-only
max-Sharpe (tangency) with the house risk-free rate; fall back to GMV on
degenerate universes (fail-open, like the rest of the stack).

WHY standalone + CLI: it must run as its own subprocess (like the R1 sleeves)
because it imports NS-5's config/frontier/data_fetcher — importing those into a
process that also imports NS-7/NS-8 collides on the `config` module name.

CLI:
  python3 frontier_sizing.py --closes <closes.csv> --risk-free <rate> \
      --mode maxsharpe --out <weights.json>
  (closes.csv: index=date, columns=tickers, one daily Close per column)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

import config
import data_fetcher
import frontier


# ── Sizing point selection ───────────────────────────────────────────────
def _sharpe_weights(closes: pd.DataFrame, tickers: List[str],
                    rf: float) -> Optional[Dict[str, float]]:
    """Long-only max-Sharpe weights on the Ledoit-Wolf covariance.

    maximize (w'μ − rf)/√(w'Σw)  s.t. Σw = 1, w ≥ 0  (SLSQP, deterministic).
    Returns None if the solve fails (degenerate) → caller falls back.
    """
    available = [t for t in tickers if t in closes.columns]
    if len(available) < 2:
        return None
    rets = data_fetcher.compute_log_returns(closes[available].copy())
    if rets.empty or len(rets) < 60:
        return None
    mu = rets.mean().to_numpy() * 252.0
    cov = frontier._cov_shrunk(rets)
    n = len(available)

    def neg_sharpe(w):
        ret = float(w @ mu) - rf
        vol = float(np.sqrt(w @ cov @ w))
        return -ret / vol if vol > 0 else 0.0

    from scipy.optimize import minimize
    cons = ({"type": "eq", "fun": lambda w: w.sum() - 1.0},)
    bounds = [(0.0, 1.0)] * n
    w0 = np.full(n, 1.0 / n)
    res = minimize(neg_sharpe, w0, method="SLSQP", bounds=bounds,
                   constraints=cons, options={"ftol": 1e-10, "maxiter": 500})
    if not res.success:
        return None
    w = res.x
    if w.sum() <= 0 or not np.all(np.isfinite(w)):
        return None
    w = np.clip(w, 0, 1) / w.sum()
    return {t: round(float(wi), 6) for t, wi in zip(available, w)}


def _gmv_weights(closes: pd.DataFrame, tickers: List[str]) -> Optional[Dict[str, float]]:
    """Global minimum-variance weights (closed-form, clipped ≥0) — the safest
    fallback for a degenerate/max-sharpe-failed universe."""
    available = [t for t in tickers if t in closes.columns]
    if len(available) < 2:
        return None
    rets = data_fetcher.compute_log_returns(closes[available].copy())
    if rets.empty or len(rets) < 60:
        return None
    cov = frontier._cov_shrunk(rets)
    n = len(available)
    inv = np.linalg.inv(cov)
    ones = np.ones(n)
    w = inv @ ones / (ones @ inv @ ones)
    w = np.clip(w, 0, 1)
    if w.sum() <= 0:
        return None
    w = w / w.sum()
    return {t: round(float(wi), 6) for t, wi in zip(available, w)}


# ── Orchestration ────────────────────────────────────────────────────────
def size_frontier(closes: pd.DataFrame, tickers: List[str],
                  mode: str = "maxsharpe",
                  rf: float = 0.0) -> Dict:
    """Return target weights for the joint universe at the chosen sizing point.

    mode:
      - 'maxsharpe'  → long-only tangency (max Sharpe). Fallback → GMV → equal-wt.
      - 'gmv'        → global minimum variance. Fallback → equal-wt.
      - 'equalweight'→ the v2 stopgap (for comparison / as a final fallback).
    Always returns a fully-invested, long-only weight dict (sum ≈ 1.0).
    """
    available = [t for t in tickers if t in closes.columns]
    if not available:
        return {"error": "no tickers with price data", "mode": mode}
    if mode == "equalweight":
        w = {t: 1.0 / len(available) for t in available}
        return {"mode": mode, "weights": w, "n": len(available),
                "source": "equalweight"}

    w = _sharpe_weights(closes, available, rf) if mode == "maxsharpe" else _gmv_weights(closes, available)
    source = "maxsharpe" if mode == "maxsharpe" else "gmv"
    if w is None:
        w = _gmv_weights(closes, available) if mode == "maxsharpe" else None
        source = "gmv" if mode == "maxsharpe" else "none"
    if w is None:
        w = {t: 1.0 / len(available) for t in available}
        source = "equalweight"
    return {"mode": mode, "weights": w, "n": len(available), "source": source,
            "risk_free": rf}


def main() -> int:
    ap = argparse.ArgumentParser(description="R2 frontier sizing")
    ap.add_argument("--closes", required=True, help="closes CSV: index=date, cols=tickers")
    ap.add_argument("--risk-free", type=float, default=0.0, help="annualized risk-free rate")
    ap.add_argument("--mode", default="maxsharpe",
                    choices=["maxsharpe", "gmv", "equalweight"])
    ap.add_argument("--out", required=True, help="output weights JSON path")
    args = ap.parse_args()

    closes = pd.read_csv(args.closes, index_col=0, parse_dates=True)
    tickers = [c for c in closes.columns]
    res = size_frontier(closes, tickers, args.mode, args.risk_free)
    Path(args.out).write_text(json.dumps(res, indent=2, default=str))
    print(f"frontier sizing ({res.get('mode')} → {res.get('source')}): "
          f"{len(res.get('weights', {}))} names, risk-free {args.risk_free:.3f}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
