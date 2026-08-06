#!/usr/bin/env python3
"""
NS-5 Portfolio — input parsing and daily-return series construction.

Roadmap Phase 2.2–2.3:
- Parse a portfolio snapshot (CSV/JSON/dict) → validated {ticker: weight}
- Build daily portfolio return series from holdings + cached Yahoo closes
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional, Union

import numpy as np
import pandas as pd

import config
import data_fetcher

# Guard: maximum tickers we'll fetch in one portfolio
MAX_TICKERS = 100


def parse_portfolio(source: Union[str, Path, dict]) -> Dict[str, float]:
    """
    Parse a portfolio snapshot into {ticker: weight}.

    Accepted sources:
      - dict:   {ticker: weight} directly
      - .csv:   columns (ticker, weight) at minimum
      - .json:  JSON obj or array of {ticker: weight, ...} / [{ticker, weight}]
    Returns dict of {ticker: float weight}. Weights are normalized to sum 1.0.
    """
    if isinstance(source, dict):
        holdings = dict(source)
    elif isinstance(source, (str, Path)) and str(source).endswith(".csv"):
        df = pd.read_csv(source)
        # Map flexible columns — look for 'ticker'/'symbol' and 'weight'/'allocation'
        tk_col = next((c for c in df.columns if c.lower() in ("ticker", "symbol")), df.columns[0])
        wt_col = next((c for c in df.columns if c.lower() in ("weight", "allocation", "pct")), df.columns[1])
        holdings = dict(zip(df[tk_col], df[wt_col]))
    elif isinstance(source, (str, Path)) and str(source).endswith(".json"):
        with open(source) as fh:
            raw = json.load(fh)
        if isinstance(raw, dict):
            # Strip metadata keys that aren't tickers
            meta_keys = {"name", "policy_name", "as_of", "risk_tolerance", "theta"}
            holdings = {k: float(v) for k, v in raw.items() if k not in meta_keys}
        elif isinstance(raw, list):
            holdings = {}
            for item in raw:
                tk = item.get("ticker") or item.get("symbol") or item.get("name")
                wt = item.get("weight") or item.get("allocation") or item.get("pct") or 0
                if tk:
                    holdings[tk] = float(wt)
        else:
            raise ValueError(f"unexpected JSON root type: {type(raw)}")
    else:
        raise ValueError(f"unsupported source type: {type(source)} — provide dict, .csv, or .json path")

    if not holdings:
        raise ValueError("no holdings found in portfolio source")
    if len(holdings) > MAX_TICKERS:
        raise ValueError(f"too many tickers ({len(holdings)} > {MAX_TICKERS})")

    # Validate and normalize
    cleaned = {}
    for ticker, weight in holdings.items():
        if not isinstance(ticker, str) or not ticker.strip():
            continue
        ticker = ticker.strip().upper()
        weight = float(weight)
        if weight < 0:
            raise ValueError(f"negative weight for {ticker}: {weight}")
        cleaned[ticker] = weight

    total = sum(cleaned.values())
    if total <= 0:
        raise ValueError("portfolio weights sum to zero")
    # Normalize
    for tk in cleaned:
        cleaned[tk] = cleaned[tk] / total

    return cleaned


def build_portfolio_returns(
    holdings: Dict[str, float],
    closes: Optional[pd.DataFrame] = None,
    force_refresh: bool = False,
) -> pd.Series:
    """
    Compute daily log-return series for a weighted portfolio.

    Args:
        holdings: {ticker: weight} from parse_portfolio()
        closes:   optional pre-loaded closes DataFrame (avoids re-fetch)
        force_refresh: re-download Yahoo data

    Returns: pd.Series of daily portfolio returns (index=date).
    """
    if closes is None or closes.empty:
        all_tickers = list(holdings.keys())
        closes = data_fetcher.get_closes(all_tickers, force_refresh=force_refresh)
    if closes.empty:
        return pd.Series(dtype=float)

    # Align closes to holdings — drop tickers we don't have prices for
    available = [t for t in holdings if t in closes.columns]
    if not available:
        return pd.Series(dtype=float)

    df = closes[available].copy()
    returns = data_fetcher.compute_log_returns(df)
    if returns.empty:
        return pd.Series(dtype=float)

    # Weighted sum: portfolio return at time t = Σ w_i * r_{i,t}
    weights = np.array([holdings[t] for t in available])
    port_ret = (returns[available] @ weights)
    port_ret = port_ret.where(np.isfinite(port_ret)).dropna()
    return port_ret


def shares_to_weights(holdings_shares: Dict[str, float],
                      closes: pd.DataFrame) -> Dict[str, float]:
    """
    Convert {ticker: shares} to {ticker: weight} using latest close prices.

    weight_i = shares_i * price_i / Σ(shares_j * price_j)
    Tickers without price data are dropped (weight zero).
    """
    if closes is None or closes.empty or not holdings_shares:
        return {}
    latest = closes.iloc[-1]
    values = {}
    for tk, shares in holdings_shares.items():
        tk = tk.strip().upper()
        if tk in latest.index and pd.notna(latest[tk]) and latest[tk] > 0:
            values[tk] = float(shares) * float(latest[tk])
    total = sum(values.values())
    if total <= 0:
        return {}
    return {tk: v / total for tk, v in values.items()}