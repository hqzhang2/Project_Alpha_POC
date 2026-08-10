"""
Alpha Terminal — Regime tab data module (display-only consumer).

Serves the NS-5 regime axis classification for the A_T regime tab:
active regime badge, 6-gauge strip, 24-month calendar, transition signal.

Design:
  - Source of truth: common/regime_store (SQLite regime_history) populated
    by common/regime_pipeline (FRED + Yahoo + RegimeClassifier). A_T is a
    READ-ONLY consumer — never fetches FRED itself.
  - The common/ package lives at the repo root; the service runs with
    env -u PYTHONPATH, so bootstrap the root (house pattern: NS-5
    theta.py does the same).
  - Fail-open: empty store / missing key -> {'regime': 'N/A', ...} +
    error key, never a crash. The page shows '--' until the pipeline runs.
  - R2: ROUTES = {'/api/regime': 'handle_regime'}; handler method on
    Handler class in server.py; module registered in
    _discover_module_routes().

Author: Junior LLM (cheap model), 2026-08-09
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

import pandas as pd  # noqa: E402


def _read_history(days: int = 730) -> pd.DataFrame:
    """Regime history from the shared SQLite store. Fail-open: empty df."""
    try:
        from common.regime_store import query_window
        return query_window(days=days)
    except Exception:
        return pd.DataFrame()


def get_regime() -> dict:
    """Latest regime classification payload.

    Returns:
        {regime, confidence, flags, cpi_yoy, gdp_qoq, unrate, curve_bp,
         baa_aaa_bp, nfci, vix, corr, wti, as_of}
        Fail-open: {'regime': 'N/A', 'error': ...}
    """
    df = _read_history(days=30)
    if df.empty:
        return {"regime": "N/A", "error": "no regime data — run the NS-5 regime pipeline"}
    row = df.iloc[-1]
    payload = {
        "regime": str(row.get("regime", "N/A")),
        "confidence": _num(row.get("confidence")),
        "flags": row.get("flags", ""),
        "cpi_yoy": _num(row.get("cpi_yoy")),
        "gdp_qoq": _num(row.get("gdp_qoq")),
        "unrate": _num(row.get("unrate")),
        "curve_bp": _num(row.get("curve_bp")),
        "baa_aaa_bp": _num(row.get("baa_aaa_bp")),
        "nfci": _num(row.get("nfci")),
        "vix": _num(row.get("vix")),
        "corr": _num(row.get("corr")),
        "wti": _num(row.get("wti")),
        "as_of": str(df.index[-1].date()) if hasattr(df.index[-1], "date") else None,
    }
    return payload


def get_regime_history(days: int = 730) -> dict:
    """Regime history for the calendar view.

    Returns:
        {history: [{date, regime, confidence, flags}, ...]} oldest→newest
        Fail-open: {'history': []}
    """
    df = _read_history(days=days)
    if df.empty:
        return {"history": []}
    rows = []
    for idx, row in df.iterrows():
        rows.append({
            "date": str(idx.date()) if hasattr(idx, "date") else str(idx)[:10],
            "regime": str(row.get("regime", "N/A")),
            "confidence": _num(row.get("confidence")),
            "flags": row.get("flags", ""),
        })
    return {"history": rows}


def _num(v):
    """float or None (SQLite may return None)."""
    if v is None:
        return None
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


# Module route registration (R2)
ROUTES = {
    '/api/regime': 'handle_regime',
    '/api/regime/history': 'handle_regime_history',
}
