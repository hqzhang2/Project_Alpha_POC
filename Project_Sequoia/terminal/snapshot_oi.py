"""
Daily per-contract OI/vol snapshot (v2.4 Phase 2). Launchd: Mon-Fri 16:30 ET.

Stores full chains (all strikes/expiries in the scan window) + ticker spot into
data/option_oi.db so option_screener can compute OI-build %, vol percentile and
the OI-up-price-flat divergence. Idempotent per date (upsert by PK).

Run: env -u PYTHONPATH <py39> snapshot_oi.py
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import option_oi_store
import options_data


def _mid(rec):
    b, a, l = rec.get("bid"), rec.get("ask"), rec.get("last")
    if b and a and b > 0 and a > 0:
        return (b + a) / 2.0
    return l or 0.0


def last_trading_day(ticker):
    """Most recent trading day with a close, via yfinance 5d history.

    The daily job runs 16:30 ET ON trading days so calendar today is correct;
    on-demand snapshots can land on weekends/holidays and must date their
    contracts with the last close (an OI snapshot Saturday reflects Friday's
    close). Fail-open: calendar today on any error.
    """
    try:
        import yfinance
        hist = yfinance.Ticker(ticker).history(period="5d")
        if hist is not None and len(hist):
            return hist.index[-1].date().isoformat()
    except Exception as e:
        print(f"last_trading_day error {ticker}: {e}")
    return datetime.date.today().isoformat()


def snapshot_ticker(provider, ticker, today=None):
    """Snapshot ONE ticker's chains into option_oi.db. Returns contracts stored.

    On-demand path (dashboard watchlist add): same per-ticker logic as run(),
    skipping expiries whose chain errors. Idempotent per (date, ticker) via
    store_snapshot's PK upsert. Fail-open: 0 on any error.
    """
    today = today or last_trading_day(ticker)
    try:
        expiries = provider.get_expirations(ticker)[: config.SCREENER_MAX_EXPIRIES]
        spot, contracts = None, []
        for exp in expiries:
            chain = provider.get_chain(ticker, exp)
            if "error" in chain:
                continue
            spot = chain.get("spot") or spot
            for side, typ in (("calls", "Call"), ("puts", "Put")):
                for r in chain.get(side, []):
                    contracts.append((exp, r.get("strike"), typ,
                                      r.get("oi") or 0, r.get("vol") or 0, _mid(r)))
        if contracts:
            return option_oi_store.store_snapshot(today, ticker, spot, contracts)
        return 0
    except Exception as e:
        print(f"snapshot error {ticker}: {e}")
        return 0


def run():
    provider = options_data.get_provider()
    option_oi_store.init_db()
    # reuse the screener's universe (watchlist + liquid pool + earnings names), cached 24h
    uni, _ = __import__("option_screener")._universe(provider)
    today = datetime.date.today().isoformat()
    total, failed = 0, 0
    for ticker in uni:
        n = snapshot_ticker(provider, ticker, today)
        total += n
        if not n:
            failed += 1
    print(f"snapshot {today}: {len(uni)} tickers, {total} contracts stored, {failed} failed")


if __name__ == "__main__":
    run()
