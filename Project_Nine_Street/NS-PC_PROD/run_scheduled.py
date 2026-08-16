#!/usr/bin/env python3
"""run_scheduled.py — NS-PC daily scheduled construct.

Runs the full stack's write path on a schedule (after NS-5's 17:45 blend, at
18:00): fetch live prices for the composed book → construct → write
paper_portfolio.json. Exits 0 on success, non-zero on failure (no partial write).

Fail-open: if NS-X/NS-5/NS-8 inputs are missing or stale, NS-PC does NOT write —
the last good book stays intact and we exit non-zero (launchd KeepAlive only
restarts on crash, not on this clean non-zero, so a stale-data day just skips).
"""
import json
import logging
import sys
from datetime import datetime

sys.path.insert(0, "/Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-PC")

import config
import constructor

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("nspc.schedule")


def _fetch_prices(tickers):
    """Last close for each ticker via yfinance (fallback: prior current_price)."""
    prices = {}
    try:
        import yfinance as yf
        for t in tickers:
            try:
                h = yf.Ticker(t).history(period="5d")
                if not h.empty:
                    prices[t] = float(h["Close"].iloc[-1])
            except Exception:
                continue
    except Exception as e:
        log.warning("yfinance unavailable: %s", e)
    return prices


def main() -> int:
    alloc, blend, signals = constructor.read_inputs()
    if alloc is None or blend is None or signals is None:
        log.warning("missing/stale inputs — no write, last book intact")
        return 1

    composed = constructor.apply_guards(constructor.compose(alloc, blend, signals))
    tickers = list(composed.keys())
    prices = _fetch_prices(tickers)

    # fallback: prior current_price for any ticker still missing
    prior = None
    try:  # prefer DB (authoritative); fall back to file
        sys.path.insert(0, "/Users/chuck/Project_Alpha_POC")
        import common.db as db
        prior = db.get_portfolio(config.PORTFOLIO_NAME)
    except Exception:
        prior = None
    if prior is None and config.PORTFOLIO_PATH.exists():
        try:
            prior = json.loads(config.PORTFOLIO_PATH.read_text())
            for t, p in prior.get("positions", {}).get("equities", {}).items():
                if t not in prices:
                    prices[t] = p.get("current_price")
        except Exception:
            prior = None
    elif prior is not None:
        for t, p in prior.get("positions", {}).get("equities", {}).items():
            if t not in prices:
                prices[t] = p.get("current_price")

    # sanity: require prices for the majority of the book, else fail-open
    if not prices or len(prices) < max(3, len(tickers) // 2):
        log.warning("too few prices (%d/%d) — no write", len(prices), len(tickers))
        return 1

    doc = constructor.build_portfolio(alloc, blend, signals, prices, prior=prior)
    constructor.write_portfolio(doc)
    log.info("wrote %d positions, nav %.2f, as_of %s",
             len(doc["positions"]["equities"]), doc["account"]["total_nav"],
             doc["account"]["last_updated"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
