#!/usr/bin/env python3
"""
NS-5 Factor Data Pipeline — Yahoo daily fetcher, return computation, caching.

Roadmap Phase 1.3:
- Fetch 2-year daily OHLCV for the factor proxy ETFs + risk-free rate via Yahoo
- Compute daily log returns
- Cache as CSV under data/cache/
- Refresh-after-close compatible (idempotent: re-run refreshes to latest bar)

Guardrails (frontier-set, do not change):
- Log returns of non-positive prices -> NaN -> dropped (never forward-filled)
- First bar of every series is NaN (no prior close) -> dropped
- Min-period guard: cached series must have >= MIN_PERIODS_PCT of the window
- No broker gateways — pure Yahoo data seam
"""
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import config

log = logging.getLogger("ns5.data_fetcher")

# ---------------------------------------------------------------------------
# Yahoo download
# ---------------------------------------------------------------------------

def _download(tickers, period=config.YF_PERIOD):
    """Download daily Close for tickers from Yahoo. Returns DataFrame (index=date)."""
    import yfinance as yf
    df = yf.download(
        list(tickers),
        period=period,
        interval=config.YF_INTERVAL,
        auto_adjust=config.YF_AUTO_ADJUST,
        progress=config.YF_PROGRESS,
        group_by="column",
        threads=True,
    )
    if df is None or df.empty:
        return pd.DataFrame()
    # yfinance 0.2.66 returns MultiIndex columns when group_by='column'
    if isinstance(df.columns, pd.MultiIndex):
        if "Close" in df.columns.get_level_values(0):
            closes = df["Close"]
        else:
            closes = df.xs("Close", axis=1, level=1) if "Close" in df.columns.get_level_values(1) else df
    else:
        closes = df[["Close"]] if "Close" in df.columns else df
    closes.index = pd.to_datetime(closes.index)
    closes = closes.tz_localize(None) if closes.index.tz is not None else closes
    # Drop all-NaN columns (tickers Yahoo failed to return)
    return closes.dropna(axis=1, how="all")


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

def _cache_path(ticker: str) -> Path:
    return config.CACHE_DIR / f"{ticker.replace('^', '')}.csv"


def _cache_age_days(path: Path) -> float:
    if not path.exists():
        return float("inf")
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    if df.empty:
        return float("inf")
    last = df.index[-1].to_pydatetime().replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last).total_seconds() / 86400.0


def get_closes(tickers, force_refresh=False):
    """
    Return a DataFrame of daily Close prices for tickers (one column per ticker),
    using cache when fresh (<= CACHE_MAX_AGE_DAYS), refreshing otherwise.
    """
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)

    missing = []
    frames = {}
    for t in tickers:
        path = _cache_path(t)
        fresh = (not force_refresh) and _cache_age_days(path) <= config.CACHE_MAX_AGE_DAYS
        if fresh:
            frames[t] = pd.read_csv(path, index_col=0, parse_dates=True)
        else:
            missing.append(t)

    if missing:
        log.info("refreshing %s from Yahoo", missing)
        downloaded = _download(missing)
        if not downloaded.empty:
            for t in missing:
                if t in downloaded.columns:
                    col = downloaded[t].dropna()
                    # min-period guard: refuse to cache a nearly-empty series
                    if len(col) < config.MIN_PERIODS_PCT * 250:
                        log.warning("ticker %s returned only %d bars; skipping cache", t, len(col))
                        continue
                    col.to_csv(_cache_path(t))
                    frames[t] = col
                else:
                    log.warning("ticker %s not returned by Yahoo", t)

    if not frames:
        return pd.DataFrame()

    closes = pd.concat(frames.values(), axis=1, keys=frames.keys(), join="outer")
    closes.columns = closes.columns.get_level_values(0) if isinstance(closes.columns, pd.MultiIndex) else closes.columns
    return closes.sort_index()


# ---------------------------------------------------------------------------
# Return computation
# ---------------------------------------------------------------------------

def compute_log_returns(closes: pd.DataFrame) -> pd.DataFrame:
    """
    Daily log returns: log(close_t / close_{t-1}).
    NaN handling (house rule): non-positive or missing prices -> NaN -> dropped,
    never forward-filled. First bar of each series is NaN by construction.
    """
    if closes.empty:
        return pd.DataFrame()
    with np.errstate(divide="ignore", invalid="ignore"):
        returns = np.log(closes / closes.shift(1))
    # Guard: log of non-positive values yields -inf/NaN — mask them out
    returns = returns.where(np.isfinite(returns))
    returns = returns.dropna(how="all")
    return returns


def load_risk_free():
    """
    Daily risk-free rate from ^IRX (13-week T-bill, annualized yield %).
    Returns a DataFrame with column 'rf' of daily rates (decimal, e.g. 0.00019).
    ^IRX is a level (yield), not a price: no log returns; divide by 252.
    """
    path = _cache_path(config.RISK_FREE_TICKER)
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fresh = path.exists() and _cache_age_days(path) <= config.CACHE_MAX_AGE_DAYS
    if not fresh:
        downloaded = _download([config.RISK_FREE_TICKER])
        if not downloaded.empty and config.RISK_FREE_TICKER in downloaded.columns:
            downloaded[[config.RISK_FREE_TICKER]].dropna().to_csv(path)
    if not path.exists():
        return pd.DataFrame(columns=["rf"])
    raw = pd.read_csv(path, index_col=0, parse_dates=True)
    if raw.empty:
        return pd.DataFrame(columns=["rf"])
    series = raw.iloc[:, 0]
    series = series.where(np.isfinite(series)).dropna()
    rf_daily = (series / 100.0) / 252.0
    return pd.DataFrame({"rf": rf_daily})


def build_factor_returns(force_refresh=False):
    """
    Build the factor daily return DataFrame: one column per factor in FACTOR_NAMES.

    MKT = SPY - rf ; SMB = IWM - SPY ; HML = VTV - VUG ; MOM = MTUM - SPY ; DUR = TLT.
    Returns (factor_returns: DataFrame, closes: DataFrame, rf: DataFrame).
    """
    closes = get_closes(list(config.FACTOR_TICKERS.keys()), force_refresh=force_refresh)
    if closes.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    rets = compute_log_returns(closes)
    if rets.empty:
        return pd.DataFrame(), closes, pd.DataFrame()

    rf = load_risk_free()
    rf_daily = rf["rf"] if not rf.empty else pd.Series(0.0, index=rets.index)

    factor_returns = pd.DataFrame(index=rets.index)
    for name, (long_tk, short_tk, kind) in config.FACTOR_DEFINITIONS.items():
        if long_tk not in rets.columns:
            continue
        if kind == "raw":
            factor_returns[name] = rets[long_tk]
        elif kind == "excess":
            rf_aligned = rf_daily.reindex(rets.index).fillna(0.0)
            factor_returns[name] = rets[long_tk] - rf_aligned
        elif kind == "spread":
            if short_tk in rets.columns:
                factor_returns[name] = rets[long_tk] - rets[short_tk]
            else:
                factor_returns[name] = rets[long_tk]
        factor_returns[name] = factor_returns[name].where(np.isfinite(factor_returns[name]))

    # Keep only rows where the union of factors is populated (drop leading NaNs)
    factor_returns = factor_returns.replace([np.inf, -np.inf], np.nan).dropna(how="all")
    return factor_returns, closes, rf


# ---------------------------------------------------------------------------
# CLI: refresh cache
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    force = "--force" in sys.argv
    factors, closes, rf = build_factor_returns(force_refresh=force)
    if factors.empty:
        log.error("no factor data — Yahoo fetch failed or cache empty")
        sys.exit(1)
    meta = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "factor_count": int(factors.shape[0]),
        "last_date": str(factors.index[-1].date()),
        "first_date": str(factors.index[0].date()),
        "factors": config.FACTOR_NAMES,
    }
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.DATA_DIR / "factor_meta.json", "w") as fh:
        json.dump(meta, fh, indent=2)
    log.info("factor cache refreshed: %d rows, %s -> %s", meta["factor_count"], meta["first_date"], meta["last_date"])


if __name__ == "__main__":
    main()
