"""ns8_grading.py — NS-8 book mark-to-market + return stream (v4.7).

Mirrors v4.6 d1_grading.py: the NS-8 weighted book (INCLUDING the SHV cash
leg — the sleeve's capital-preservation position) is marked to market daily
after close, producing a REALIZED daily return stream stored as strategy 'ns8'
in the NS-DB strategy_returns table (common.db.write_strategy_returns). That
stream upgrades NS-X's SPY-proxy ns8_returns to the realized weighted book.

⚠ PRICE SOURCE — deliberately NOT D1's A_T fundamentals_hist.db: that store
holds only ~506 S&P500 constituents, ZERO ETFs. NS-8's universe (SPY/EFA/IEF/
VNQ/DBC/SHV) is absent there. This module reads NS-8's OWN closes:
  1. the live closes fetched by pipeline.run_refresh (passed in), or
  2. data/ns8_hist_closes.json cache (refreshed by the daily refresh job).

Return definition: r(t) = Σ w_i × (close_i(t)/close_i(t-1) − 1), weights from
the CURRENT signals.json (incl. SHV). Realized-only: returns are computed from
the book's as_of FORWARD — no look-ahead backtest of today's weights.

Fail-open everywhere: missing prices/DB → no write, never crash.
DPF-owned methodology (implemented under PM authorization for v4.7).
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import config

log = logging.getLogger("ns8.ns8_grading")

STRATEGY_ID = "ns8"          # NS-X registry key: ns8_returns


# ── Price source (NS-8's own store — NOT fundamentals_hist.db) ───────────
def _load_closes_from_cache(
        path: Optional[Path] = None) -> Dict[str, Dict[str, float]]:
    """{ticker: {iso_date: close}} from the NS-8 historical closes cache."""
    p = Path(path or config.HIST_CLOSES_PATH)
    try:
        with open(p) as fh:
            data = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        log.warning("NS-8 closes cache unavailable (%s) — no MtM", exc)
        return {}
    dates: List[str] = data.get("dates", [])
    closes_raw: Dict[str, List] = data.get("closes", {})
    out: Dict[str, Dict[str, float]] = {}
    for t in data.get("tickers", []):
        series = {d: float(v) for d, v in zip(dates, closes_raw.get(t, []))
                  if v is not None}
        if series:
            out[t] = series
    return out


def book_daily_returns(weights: Dict[str, float],
                       closes_by_ticker: Dict[str, Dict[str, float]]
                       ) -> List[Dict[str, Any]]:
    """Weighted daily returns over dates where EVERY weighted ticker has a price.

    Only dates where every weighted ticker (INCL. the SHV cash leg) is priceable
    are used — no partial books. A ticker whose series is shorter (e.g. SHV's
    cache) therefore truncates the common window; that is intentional and
    logged so the stream isn't misread as covering the full period. A zero or
    missing prev close skips that day's return (no ZeroDivisionError).
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
    if weights and days:
        full = None
        for t in weights:
            s = set(closes_by_ticker.get(t, {}).keys())
            full = s if full is None else (full | s)
        if full and len(days) < len(full):
            log.info("MtM window truncated to %d days (common prices); "
                     "full union %d — SHV/partial series limit", len(days), len(full))
    rows: List[Dict[str, Any]] = []
    for prev, cur in zip(days, days[1:]):
        r = 0.0
        for t, w in weights.items():
            px = closes_by_ticker[t]
            if not px.get(prev):
                log.warning("skip %s->%s: no valid prev close for %s", prev, cur, t)
                r = None
                break
            r += w * (px[cur] / px[prev] - 1.0)
        if r is None:
            continue  # fail-open: drop this day rather than corrupt it
        rows.append({"date": cur, "return": round(r, 8),
                     "source": "ns8_book_mtm"})
    return rows


def mark_to_market(basket_path: Optional[Path] = None,
                   cache_path: Optional[Path] = None,
                   from_as_of: bool = True) -> Optional[List[Dict]]:
    """Read signals.json → weighted daily returns (incl. SHV). None if no book."""
    p = Path(basket_path or config.SIGNALS_PATH)
    try:
        doc = json.loads(p.read_text())
    except Exception as exc:  # noqa: BLE001
        log.warning("no signals doc (%s) — nothing to mark", exc)
        return None
    weights = doc.get("weights") or {}
    if not any(w > 0 for w in weights.values()):
        return None
    closes = _load_closes_from_cache(cache_path)
    rows = book_daily_returns(weights, closes)
    if not rows:
        log.warning("no overlapping price history — no returns computed")
        return None
    if from_as_of:
        as_of = (doc.get("as_of") or "")[:10]
        if not as_of:
            log.warning("book has no as_of — refusing look-ahead MtM")
            return None
        rows = [r for r in rows if r["date"] >= as_of]
        if not rows:
            log.warning("no returns on/after book as_of %s — nothing to write", as_of)
            return None
    return rows


def persist_returns(rows: List[Dict[str, Any]]) -> bool:
    """Write the NS-8 realized stream into NS-DB strategy_returns ('ns8')."""
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


def update_closes_cache(lookback_days: int = 30) -> int:
    """Refresh data/ns8_hist_closes.json with recent daily closes (yfinance).

    The MtM realized-only guard requires prices ON/after the book's as_of; the
    static walk-forward cache goes stale, so the daily refresh job tops it up
    before grading. Rebuilds each series over the UNION of cached dates and
    newly fetched dates, keeping rows date-aligned. Returns the number of NEW
    dates appended. Fail-open: any error leaves the existing cache untouched.
    """
    import datetime as _dt
    p = Path(config.HIST_CLOSES_PATH)
    try:
        doc = json.loads(p.read_text())
    except Exception as exc:  # noqa: BLE001
        log.warning("closes cache unreadable (%s) — skip top-up", exc)
        return 0
    old_dates: List[str] = list(doc.get("dates", []))
    if not old_dates or old_dates[-1] >= (
            _dt.date.today() - _dt.timedelta(days=2)).isoformat():
        return 0                      # fresh enough (weekend-safe)
    try:
        import yfinance as yf
        end = _dt.date.today().isoformat()
        start = (_dt.date.today() - _dt.timedelta(days=lookback_days)).isoformat()
        tickers: List[str] = doc["tickers"]
        raw = yf.download(tickers, start=start, end=end,
                          auto_adjust=True, progress=False, group_by="ticker")
        # collect new {date: close} per ticker
        new_by_ticker: Dict[str, Dict[str, float]] = {t: {} for t in tickers}
        for t in tickers:
            frame = raw[t]["Close"].dropna() if len(tickers) > 1 \
                else raw["Close"].dropna()
            for d, v in frame.items():
                iso = str(d)[:10]
                if iso > old_dates[-1]:
                    new_by_ticker[t][iso] = float(v)
        new_dates = sorted({d for m in new_by_ticker.values() for d in m})
        if not new_dates:
            return 0
        all_dates = old_dates + new_dates
        old_closes: Dict[str, List] = doc.get("closes", {})
        merged: Dict[str, List] = {}
        for t in tickers:
            series = list(old_closes.get(t, []))
            series += [None] * (len(old_dates) - len(series))   # pad legacy gaps
            for i, d in enumerate(new_dates):
                series.append(new_by_ticker[t].get(d))          # None if missing
            merged[t] = series
        p.write_text(json.dumps({
            "tickers": tickers, "dates": all_dates, "closes": merged}))
        log.info("closes cache topped up: %d new dates %s..%s",
                 len(new_dates), new_dates[0], new_dates[-1])
        return len(new_dates)
    except Exception as exc:  # noqa: BLE001
        log.warning("cache top-up failed (%s) — keeping stale cache", exc)
        return 0


def main() -> int:
    update_closes_cache()
    rows = mark_to_market()
    if rows is None:
        print("NS-8 MtM: no book or no prices — nothing written")
        return 1
    ok = persist_returns(rows)
    first, last = rows[0]["date"], rows[-1]["date"]
    print(f"NS-8 MtM: {len(rows)} daily returns {first}..{last} "
          f"-> strategy_returns[{STRATEGY_ID}] ({'written' if ok else 'FAILED'})")
    return 0 if ok else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
