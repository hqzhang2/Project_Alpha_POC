#!/usr/bin/env python3
"""
NS-5 Factor Environment Monitors — volatility regimes and correlation shifts.

Roadmap Phase 1.5:
- Rolling 60d / 250d vol per factor (ddof=1)
- Rolling 120d pairwise factor correlations
- Vol ratio (60d/250d) and correlation-shift signals, frontier thresholds

Guardrails (frontier-set, do not change):
- Window sizes: VOL_WINDOW_SHORT=60, VOL_WINDOW_LONG=250, CORR_WINDOW=120
- Vol uses ddof=1; correlation uses np.corrcoef
- Signal thresholds: VOL_RATIO_THRESHOLD=1.5, CORR_SHIFT_THRESHOLD=0.3
"""
import numpy as np
import pandas as pd

import config


def rolling_vol(factor_returns: pd.DataFrame, window: int):
    """Annualized rolling vol (ddof=1), sqrt(252) scaling. DataFrame indexed by date."""
    if factor_returns.empty:
        return pd.DataFrame()
    vol = factor_returns.rolling(window, min_periods=int(window * config.MIN_PERIODS_PCT)).std(ddof=1)
    return vol * np.sqrt(252)


def vol_regime_series(factor_returns: pd.DataFrame):
    """
    Vol ratio series per factor: rolling 60d vol / rolling 250d vol.
    A ratio > VOL_RATIO_THRESHOLD means short-term vol is 1.5x the long-term level
    -> factor volatility regime has shifted.
    Returns DataFrame of ratios (same index as input), NaN where long vol not populated.
    """
    short_vol = rolling_vol(factor_returns, config.VOL_WINDOW_SHORT)
    long_vol = rolling_vol(factor_returns, config.VOL_WINDOW_LONG)
    ratio = short_vol / long_vol
    return ratio.replace([np.inf, -np.inf], np.nan)


def rolling_corr(factor_returns: pd.DataFrame, window: int = None):
    """
    Rolling pairwise correlation per factor pair (window default CORR_WINDOW=120).
    Returns dict: {(f1, f2): pd.Series of correlation}. Pairs are (f1 < f2) ordered.
    """
    window = window or config.CORR_WINDOW
    factors = list(factor_returns.columns)
    pairs = {}
    min_periods = int(window * config.MIN_PERIODS_PCT)
    for i, f1 in enumerate(factors):
        for f2 in factors[i + 1:]:
            # pairwise rolling corr via manual z-scoring (pandas .corr() has no rolling window)
            a = factor_returns[f1]
            b = factor_returns[f2]
            mu_a = a.rolling(window, min_periods=min_periods).mean()
            mu_b = b.rolling(window, min_periods=min_periods).mean()
            sd_a = a.rolling(window, min_periods=min_periods).std(ddof=1)
            sd_b = b.rolling(window, min_periods=min_periods).std(ddof=1)
            cov = (a * b).rolling(window, min_periods=min_periods).mean() - mu_a * mu_b
            corr = cov / (sd_a * sd_b)
            pairs[(f1, f2)] = corr.replace([np.inf, -np.inf], np.nan)
    return pairs


def correlation_shift_series(factor_returns: pd.DataFrame):
    """
    Corr shift per pair: corr_60d - corr_250d (both rolling).
    |shift| > CORR_SHIFT_THRESHOLD means the pairwise relationship moved materially.
    Returns DataFrame with MultiIndex columns (f1, f2), indexed by date.
    """
    pairs_short = rolling_corr(factor_returns, config.VOL_WINDOW_SHORT)
    pairs_long = rolling_corr(factor_returns, config.VOL_WINDOW_LONG)
    frames = {}
    for pair in pairs_short:
        if pair in pairs_long:
            shift = pairs_short[pair] - pairs_long[pair]
            frames[pair] = shift.replace([np.inf, -np.inf], np.nan)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames.values(), axis=1, keys=frames.keys())
    out.columns = pd.MultiIndex.from_tuples(out.columns)
    return out


def environment_summary(factor_returns: pd.DataFrame, as_of=None):
    """
    One-shot environment snapshot at `as_of` (default: last available date).
    Returns dict:
      {as_of, vol_60d, vol_250d, vol_ratio, corr_120d, corr_shift_60_250,
       flags: {factor_vol_ratios: {...}, corr_shifts: {...}}}
    """
    if factor_returns.empty:
        return {"as_of": str(as_of), "error": "no factor data"}
    as_of = as_of or factor_returns.index[-1]
    vol_short = rolling_vol(factor_returns, config.VOL_WINDOW_SHORT).loc[as_of] if as_of in factor_returns.index else None
    vol_long = rolling_vol(factor_returns, config.VOL_WINDOW_LONG).loc[as_of] if as_of in factor_returns.index else None
    ratio = vol_regime_series(factor_returns).loc[as_of] if as_of in factor_returns.index else None

    corr = rolling_corr(factor_returns, config.CORR_WINDOW)
    corr_now = {f"{k[0]}-{k[1]}": float(v.loc[as_of]) for k, v in corr.items() if as_of in v.index and not np.isnan(v.loc[as_of])}

    shift = correlation_shift_series(factor_returns)
    shifts = {}
    if not shift.empty and as_of in shift.index:
        for col in shift.columns:
            val = shift.loc[as_of, col]
            if not np.isnan(val):
                shifts[f"{col[0]}-{col[1]}"] = float(val)

    flags = {"factor_vol_ratios": {}, "corr_shifts": {}}
    if ratio is not None:
        for f, v in ratio.items():
            if not np.isnan(v):
                flags["factor_vol_ratios"][f] = float(v) > config.VOL_RATIO_THRESHOLD
    for k, v in shifts.items():
        flags["corr_shifts"][k] = abs(v) > config.CORR_SHIFT_THRESHOLD

    return {
        "as_of": str(as_of.date()),
        "vol_60d_ann": {k: float(v) for k, v in vol_short.items()} if vol_short is not None else {},
        "vol_250d_ann": {k: float(v) for k, v in vol_long.items()} if vol_long is not None else {},
        "vol_ratio_60_250": {k: float(v) for k, v in ratio.items()} if ratio is not None else {},
        "corr_120d": corr_now,
        "corr_shift_60_250": shifts,
        "flags": flags,
    }
