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


def run():
    provider = options_data.get_provider()
    option_oi_store.init_db()
    # reuse the screener's universe (watchlist + liquid pool + earnings names), cached 24h
    uni, _ = __import__("option_screener")._universe(provider)
    today = datetime.date.today().isoformat()
    total, failed = 0, 0
    for ticker in uni:
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
                total += option_oi_store.store_snapshot(today, ticker, spot, contracts)
        except Exception as e:
            failed += 1
            print(f"snapshot error {ticker}: {e}")
    print(f"snapshot {today}: {len(uni)} tickers, {total} contracts stored, {failed} failed")


if __name__ == "__main__":
    run()
