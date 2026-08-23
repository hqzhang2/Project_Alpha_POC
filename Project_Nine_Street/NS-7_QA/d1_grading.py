"""d1_grading.py — DeltaOne basket mark-to-market + return stream (v4.6).

The individual-grading foundation (research_d1_basket_v46.md §5): the D1
weighted book is marked to market daily after close, producing a REALIZED
daily return stream stored as strategy 'ns7' in the NS-DB strategy_returns
table. That stream is what NS-X rotates on and what per-strategy grading
compares against SPY — replacing the old SPY-proxy/walkforward seed.

Return definition: basket_return(t) = Σ w_i × price_return_i(t), weights from
the CURRENT d1_basket.json (the live book). History: the A_T point-in-time
price store (fundamentals_hist.db prices table) supplies closes; the basket
doc records its selection_as_of so a replay never mixes books across rebalances
(v4.6 ships current-book MtM; full book-history replay lands with the
walk-forward harness).

Fail-open everywhere: missing closes/DB → no write, never crash.
DPF-owned methodology.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import config

log = logging.getLogger("ns7.d1_grading")

STRATEGY_ID = "ns7"          # the D1 basket IS strategy ns7 in NS-X's registry


# ── Price source (A_T point-in-time store, read-only house pattern) ──────
def _load_closes(tickers: List[str],
                 db_path: Optional[Path] = None) -> Dict[str, Dict[str, float]]:
    """{ticker: {iso_date: close}} for the requested tickers. Fail-open {}."""
    path = Path(db_path or config.AT_FUNDAMENTALS_DB)
    out: Dict[str, Dict[str, float]] = {}
    try:
        conn = sqlite3.connect(str(path))
        with conn:
            for t in tickers:
                rows = conn.execute(
                    "SELECT date, close FROM prices WHERE ticker=? ORDER BY date",
                    (t.upper(),)).fetchall()
                if rows:
                    out[t] = {d: float(c) for d, c in rows}
        conn.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("price store unavailable (%s) — no MtM", exc)
    return out


def basket_daily_returns(weights: Dict[str, float],
                         closes_by_ticker: Dict[str, Dict[str, float]]
                         ) -> List[Dict[str, Any]]:
    """Weighted daily returns over the union of available price dates.

    Only dates where EVERY weighted ticker has a price are usable (no partial
    books); the earliest such date is the base (return 0 dropped).
    """
    if not weights:
        return []
    common_dates = None
    for t in weights:
        dates = set(closes_by_ticker.get(t, {}).keys())
        common_dates = dates if common_dates is None else (common_dates & dates)
    if not common_dates:
        return []
    days = sorted(common_dates)
    rows: List[Dict[str, Any]] = []
    for prev, cur in zip(days, days[1:]):
        r = 0.0
        for t, w in weights.items():
            px = closes_by_ticker[t]
            r += w * (px[cur] / px[prev] - 1.0)
        rows.append({"date": cur, "return": round(r, 8),
                     "source": "d1_basket_mtm"})
    return rows


def mark_to_market(basket_path: Optional[Path] = None,
                   db_path: Optional[Path] = None) -> Optional[List[Dict]]:
    """Read d1_basket.json → weighted daily returns. None if no basket."""
    p = Path(basket_path or config.D1_BASKET_PATH)
    db_default = db_path or getattr(config, "D1_MTM_PRICES_DB", None) \
        or config.AT_FUNDAMENTALS_DB
    try:
        doc = json.loads(p.read_text())
    except Exception as exc:  # noqa: BLE001
        log.warning("no basket (%s) — nothing to mark", exc)
        return None
    weights = doc.get("weights") or {}
    if not weights:
        return None
    closes = _load_closes(list(weights.keys()), Path(db_default))
    rows = basket_daily_returns(weights, closes)
    if not rows:
        log.warning("no overlapping price history — no returns computed")
        return None
    return rows


def persist_returns(rows: List[Dict[str, Any]]) -> bool:
    """Write the D1 realized stream into NS-DB strategy_returns ('ns7')."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
        import common.db as db
        ok = db.write_strategy_returns(STRATEGY_ID, rows)
        if not ok:
            log.warning("strategy_returns write failed (DB down?) — fail-open")
        return ok
    except Exception as exc:  # noqa: BLE001
        log.warning("strategy_returns write error (%s)", exc)
        return False


def main() -> int:
    rows = mark_to_market()
    if rows is None:
        print("D1 MtM: no basket or no prices — nothing written")
        return 1
    ok = persist_returns(rows)
    first, last = rows[0]["date"], rows[-1]["date"]
    print(f"D1 MtM: {len(rows)} daily returns {first}..{last} "
          f"-> strategy_returns[{STRATEGY_ID}] ({'written' if ok else 'FAILED'})")
    return 0 if ok else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
