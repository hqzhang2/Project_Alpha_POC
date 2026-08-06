"""
sp500_history.py — Point-in-time S&P 500 membership (survivorship-aware
universe for the framework study v2).

Reconstructs membership as of any date from the CURRENT constituent list
plus Wikipedia's "Selected changes to the list of S&P 500 components"
table (additions/removals with dates), walking backward:

    members(D) = current ∪ {removed | change.date > D} − {added | change.date > D}

Caveats (documented): ticker symbols are the CURRENT ones (renames are not
retro-mapped); removals outside index-change events (pure delistings) may
be under-represented. Approximation — good enough to measure the direction
and rough magnitude of survivorship bias.

Cache: data/sp500_history.json {"current": [...], "changes": [[date, added,
removed], ...]}. CLI: python3 sp500_history.py rebuilds the cache.
"""
import json
import os
import time
from datetime import datetime

import requests

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CACHE = os.path.join(DATA_DIR, "sp500_history.json")
URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
HEADERS = {"User-Agent": "AlphaTerminal/1.0 research@example.com"}
TTL = 7 * 24 * 3600   # weekly refresh is plenty


def fetch_and_cache(force=False):
    """Rebuild data/sp500_history.json. Returns the parsed dict."""
    if not force and os.path.exists(CACHE):
        if time.time() - os.path.getmtime(CACHE) < TTL:
            return json.load(open(CACHE))
    import io
    import pandas as pd
    html = requests.get(URL, headers=HEADERS, timeout=30).text
    tables = pd.read_html(io.StringIO(html))

    # current constituents (first table: Symbol, Security, ...)
    current = [str(s).upper().replace(".", "-")
               for s in tables[0]["Symbol"].tolist()]

    # selected-changes table: Date | Added Ticker | Added Security |
    # Removed Ticker | Removed Security | Reason (empty date = continuation)
    changes = []
    last_date = None
    for row in tables[1].itertuples(index=False):
        date = str(row[0]).strip()
        if date and date.lower() != "nan":
            try:
                last_date = datetime.strptime(date, "%B %d, %Y").strftime("%Y-%m-%d")
            except ValueError:
                continue
        if not last_date:
            continue
        added = str(row[1]).strip().upper().replace(".", "-") if str(row[1]).lower() != "nan" else ""
        removed = str(row[3]).strip().upper().replace(".", "-") if str(row[3]).lower() != "nan" else ""
        if added or removed:
            changes.append([last_date, added or None, removed or None])
    changes.sort(key=lambda c: c[0])
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CACHE, "w") as f:
        json.dump({"current": current, "changes": changes}, f)
    return {"current": current, "changes": changes}


def members_on(date_str, data=None):
    """Set of S&P 500 tickers as of date_str (YYYY-MM-DD)."""
    data = data or fetch_and_cache()
    members = set(data["current"])
    for date, added, removed in data["changes"]:
        if date > date_str:
            if removed:
                members.add(removed)
            if added:
                members.discard(added)
    return members


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    d = fetch_and_cache(force=True)
    print(f"current: {len(d['current'])} | changes: {len(d['changes'])}")
    for probe in ["2016-04-01", "2020-04-01", "2026-04-01"]:
        m = members_on(probe, d)
        print(f"{probe}: {len(m)} members | "
              f"OXY in: {'OXY' in m}, META in: {'META' in m}")
