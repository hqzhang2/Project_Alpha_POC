"""
NS-5 Regime Pipeline — fetch→classify→store integration.

Orchestrates the full daily pipeline:
  1. fetch_regime_data() — FRED + Yahoo + derived series
  2. RegimeClassifier.classify_dataframe() — 5-step detection
  3. Upsert each day into regime_history store
  4. Return the full regime history DataFarme

JUNIOR (cheap model): mechanics only.
"""
from __future__ import annotations

import pandas as pd

from common.regime_fetcher import fetch_regime_data
from common.regime_model import RegimeClassifier
from common.regime_store import query_window, upsert


def run_regime_pipeline(days_back: int = 750) -> pd.DataFrame:
    """Run the full fetch → classify → store pipeline.

    Args:
        days_back: lookback window for FRED/Yahoo fetch.

    Returns:
        DataFrame of regime history (all stored days, from DB).
        Empty DataFrame on failure (fail-open).
    """
    # 1. Fetch
    daily_df = fetch_regime_data(days_back=days_back)
    if daily_df.empty:
        return query_window(days=days_back)

    # 2. Classify
    classifier = RegimeClassifier()
    classified = classifier.classify_dataframe(daily_df)

    # 3. Store each day
    for idx, row in classified.iterrows():
        date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
        store_row = {
            "regime": row.get("regime"),
            "confidence": row.get("confidence"),
            "flags": row.get("flags", ""),
            "cpi_yoy": _safe_float(row.get("CPI_YOY")),
            "gdp_qoq": _safe_float(row.get("GDP_QOQ_ANN")),
            "unrate": _safe_float(row.get("UNRATE")),
            "curve_bp": _safe_float(row.get("2S10S", 0) * 100 if row.get("2S10S") else None),
            "baa_aaa_bp": _safe_float(row.get("BAA_AAA", 0) * 100 if row.get("BAA_AAA") else None),
            "nfci": _safe_float(row.get("NFCI")),
            "vix": _safe_float(row.get("VIX")),
            "corr": _safe_float(row.get("STOCK_BOND_CORR")),
            "wti": _safe_float(row.get("DCOILWTICO")),
        }
        upsert(date_str, store_row)

    # 4. Return full stored history
    return query_window(days=days_back)


def _safe_float(val) -> float | None:
    """Convert to float, return None for NaN/inf."""
    if val is None:
        return None
    try:
        import math
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, 4)
    except (TypeError, ValueError):
        return None
