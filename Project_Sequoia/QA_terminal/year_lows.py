"""
52-week-low scanner for Alpha Terminal.

Thin wrapper around snapshot.py — scan finviz for "New Low" signal,
enrich with yfinance trailing low, persist to year_lows table.
"""

import logging
from snapshot import (
    scan_candidates, enrich_yfinance, build_rows,
    store_today, snapshot_date
)
import config

logger = logging.getLogger("alpha-terminal.year-lows")

AT_LOW_THRESHOLD_PCT = config.AT_LOW_THRESHOLD_PCT
LOW_WINDOW = config.LOW_WINDOW


def scan_year_lows(threshold_pct=AT_LOW_THRESHOLD_PCT):
    """Build rows for all NYSE/NASDAQ new-low candidates."""
    candidates = scan_candidates("New Low")
    enrich_fn = lambda t, p: enrich_yfinance(t, p, window=LOW_WINDOW, agg="min")
    return build_rows(candidates, enrich_fn, threshold_pct, col_prefix="low", pct_key="pct_from_low")


def store_today_snapshot(threshold_pct=AT_LOW_THRESHOLD_PCT, force=False):
    """Scan + store the snapshot for today (EST) if not already stored."""
    import db
    return store_today(
        table_name="year_lows",
        get_fn=db.get_year_lows,
        store_fn=db.store_year_lows,
        scan_signal="New Low",
        enrich_fn=lambda t, p: enrich_yfinance(t, p, window=LOW_WINDOW, agg="min"),
        threshold_pct=threshold_pct,
        logger_name="year-lows",
        force=force,
    )


# Module route registration (R2)
ROUTES = {
    '/api/year-lows': 'handle_year_lows',
    '/api/year-lows/trend': 'handle_year_lows_trend',
}


def get_trend():
    """Per-date sector counts for the 52W-low trend chart."""
    import db
    return db.get_sector_trend('year_lows', 'pct_from_low', '<=')
