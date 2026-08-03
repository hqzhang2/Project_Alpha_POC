"""
52-week-high scanner for Alpha Terminal.

Thin wrapper around snapshot.py — scan finviz for "New High" signal,
enrich with yfinance trailing high, persist to year_highs table.
"""

import logging
from snapshot import (
    scan_candidates, enrich_yfinance, build_rows,
    store_today, snapshot_date
)
import config

logger = logging.getLogger("alpha-terminal.year-highs")

AT_HIGH_THRESHOLD_PCT = config.AT_HIGH_THRESHOLD_PCT
HIGH_WINDOW = config.HIGH_WINDOW


def scan_year_highs(threshold_pct=AT_HIGH_THRESHOLD_PCT):
    """Build rows for all NYSE/NASDAQ new-high candidates.

    Each row: dict(ticker, exchange, sector, company, close, high_52w,
    pct_off, volume, market_cap).
    """
    candidates = scan_candidates("New High")
    enrich_fn = lambda t, p: enrich_yfinance(t, p, window=HIGH_WINDOW, agg="max")
    return build_rows(candidates, enrich_fn, threshold_pct, col_prefix="high", pct_key="pct_off")


def store_today_snapshot(threshold_pct=AT_HIGH_THRESHOLD_PCT, force=False):
    """Scan + store the snapshot for today (EST) if not already stored."""
    import db
    return store_today(
        table_name="year_highs",
        get_fn=db.get_year_highs,
        store_fn=db.store_year_highs,
        scan_signal="New High",
        enrich_fn=lambda t, p: enrich_yfinance(t, p, window=HIGH_WINDOW, agg="max"),
        threshold_pct=threshold_pct,
        logger_name="year-highs",
        force=force,
    )


# Module route registration (R2)
ROUTES = {
    '/api/year-highs': 'handle_year_highs',
}