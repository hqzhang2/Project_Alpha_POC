#!/usr/bin/env python3
"""
NS-5 Regression Engine — OLS factor loading estimation.

Roadmap Phase 1.4:
- Single-window OLS: loading_vector, r_squared
- Rolling-window OLS (250-day window, monthly step)

Guardrails (frontier-set, do not change):
- Window sizes from config (REGRESSION_WINDOW, REGRESSION_STEP)
- Design matrix: intercept + factors in config.REGRESSORS order
- Deterministic: np.linalg.lstsq, no randomness
- NaN guard: rows with any NaN in y or X are dropped before each fit
"""
import numpy as np
import pandas as pd

import config


def _clean_window(y: pd.Series, X: pd.DataFrame) -> tuple:
    """Align y and X on common index, drop rows with any NaN/inf. Returns (y_arr, X_arr)."""
    df = pd.concat([y.rename("y"), X], axis=1, join="inner")
    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.dropna()
    return df["y"].to_numpy(dtype=float), df[config.REGRESSORS].to_numpy(dtype=float)


def regress(portfolio_returns: pd.Series, factor_returns: pd.DataFrame):
    """
    Single-window OLS of portfolio_returns on the 5 factors + intercept.

    Args:
        portfolio_returns: pd.Series of daily returns (index=date)
        factor_returns:   pd.DataFrame of factor daily returns (index=date, cols=FACTOR_NAMES)

    Returns:
        dict: {beta: {factor_name: float, ...}, alpha: float,
               r_squared: float, n_obs: int, se: {factor_name: float, ...}}
        Returns None if insufficient data (< 60 obs after cleaning).
    """
    if factor_returns.empty or portfolio_returns.empty:
        return None
    # Subset factor columns to the known factor names present
    cols = [c for c in config.FACTOR_NAMES if c in factor_returns.columns]
    if not cols:
        return None
    X_raw = factor_returns[cols].copy()
    X_raw.insert(0, "intercept", 1.0)

    y_arr, X_arr = _clean_window(portfolio_returns, X_raw)
    if len(y_arr) < 60:
        return None

    coefs, residuals, rank, _ = np.linalg.lstsq(X_arr, y_arr, rcond=None)
    if rank < X_arr.shape[1]:
        return None

    # R^2
    y_hat = X_arr @ coefs
    ss_res = float(np.sum((y_arr - y_hat) ** 2))
    ss_tot = float(np.sum((y_arr - np.mean(y_arr)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    # Standard errors (for deviation significance checks later)
    n, p = X_arr.shape
    if n > p:
        sigma2 = ss_res / (n - p)
        cov = sigma2 * np.linalg.inv(X_arr.T @ X_arr)
        se = np.sqrt(np.diag(cov))
    else:
        se = np.full(p, np.nan)

    names = ["intercept"] + cols
    return {
        "beta": {name: float(b) for name, b in zip(names[1:], coefs[1:])},
        "alpha": float(coefs[0]),
        "r_squared": float(r_squared),
        "n_obs": int(n),
        "se": {name: float(s) for name, s in zip(names[1:], se[1:])},
    }


def rolling_regress(portfolio_returns: pd.Series, factor_returns: pd.DataFrame,
                    window: int = None, step: int = None):
    """
    Rolling-window OLS. Returns a DataFrame indexed by window-end date with
    columns per factor beta + alpha + r_squared + n_obs.

    Defaults: config.REGRESSION_WINDOW (250d), config.REGRESSION_STEP (21d).
    """
    window = window or config.REGRESSION_WINDOW
    step = step or config.REGRESSION_STEP

    if factor_returns.empty or portfolio_returns.empty:
        return pd.DataFrame()

    rows = []
    idx = portfolio_returns.index.sort_values()
    # Anchor windows at the last bar, stepping back by `step`
    for end_pos in range(window, len(idx) + 1, step):
        end_date = idx[end_pos - 1]
        start_date = idx[end_pos - window]
        y_win = portfolio_returns.loc[start_date:end_date]
        x_win = factor_returns.loc[start_date:end_date]
        if len(y_win) < 60:
            continue
        result = regress(y_win, x_win)
        if result is None:
            continue
        row = {"date": end_date, "alpha": result["alpha"],
               "r_squared": result["r_squared"], "n_obs": result["n_obs"]}
        row.update(result["beta"])
        rows.append(row)

    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows).set_index("date")
    out.index = pd.to_datetime(out.index)
    return out
