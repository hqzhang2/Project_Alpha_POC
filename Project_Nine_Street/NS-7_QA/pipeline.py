"""pipeline.py — NS-7 data pipeline: universe → facts → league → momentum → selection.

The productionization of DESIGN.md §3-5. Reads A_T's point-in-time store
(fundamentals_hist.db) READ-ONLY — NS-7 never writes to A_T stores (decoupled
file/db-read pattern, same as NS-6 reading NS-5's portfolios.json). Maintains
NS-7's own league/volume/selection state in data/ns7.db and emits
data/selection.json — the growth-sleeve feed for NS-5's frontier.

Refresh steps (run daily; CLI:  python3 pipeline.py):
  1. Universe assembly — candidate set = SP500 current ∪ A_T annual-store tickers
  2. Volume refresh    — yfinance fetch for tickers whose volume is stale;
                         SYSTEMIC failure → U3 waived for this refresh (never
                         mass-demote the book on a data outage)
  3. Facts             — point-in-time per ticker (market cap, EPS, CFO,
                         in_sp500, 20d avg volume); 730-day staleness guard
                         (A_T convention: stale books are not point-in-time)
  4. League update     — fresh entry / transition / re-admission + tenure clocks
  5. Momentum          — skip-month 126/21 on Major names with a full series
  6. Selection         — rank + quality veto + top-N → selection.json + store

No drawdown logic here (guardrail G6). NS-7 emits signals; NS-6 owns the tail.
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional

import config
import selector
import store
import universe

log = logging.getLogger("ns7.pipeline")

# ── A_T store access (read-only) ────────────────────────────────────────
def at_conn() -> sqlite3.Connection:
    """Read-only connection to A_T's point-in-time fundamentals store."""
    return sqlite3.connect(f"file:{config.AT_FUNDAMENTALS_DB}?mode=ro", uri=True)


def sp500_current() -> List[str]:
    """Current SP500 constituents from A_T's weekly-refreshed cache."""
    try:
        if config.AT_SP500_CACHE.exists():
            data = json.loads(config.AT_SP500_CACHE.read_text())
            if isinstance(data, list):
                return [str(s).upper().replace(".", "-") for s in data]
    except Exception as exc:  # noqa: BLE001
        log.warning("sp500 cache read failed: %s", exc)
    # Fallback: ask A_T's sp500_history module to fetch (cached weekly).
    try:
        sys.path.insert(0, str(config.AT_SP500_CACHE.parent.parent))
        import sp500_history  # type: ignore
        data = sp500_history.fetch_and_cache()
        return [str(s).upper().replace(".", "-") for s in data.get("current", [])]
    except Exception as exc:  # noqa: BLE001
        log.warning("sp500 fallback fetch failed: %s", exc)
        return []


def annual_tickers() -> List[str]:
    """All tickers with any annual row in the A_T store."""
    conn = at_conn()
    try:
        return [r[0] for r in conn.execute("SELECT DISTINCT ticker FROM annual")]
    finally:
        conn.close()


def snapshot_on(ticker: str, as_of: str) -> Optional[dict]:
    """Latest annual row with filed <= as_of (point-in-time). None if none."""
    conn = at_conn()
    try:
        cur = conn.execute(
            "SELECT * FROM annual WHERE ticker = ? AND filed <= ? "
            "ORDER BY period_end DESC LIMIT 1", (ticker.upper(), as_of))
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    finally:
        conn.close()


def snapshot_metrics_on(ticker: str, as_of: str) -> Optional[dict]:
    """Eligibility metrics with LAST-KNOWN-GOOD fill (data-quality layer).

    The newest annual row wins for period_end/filed (the staleness clock),
    but each metric (eps, cfo, shares) falls back to the most recent row
    filed <= as_of that actually reports it. Rationale (walk-forward
    finding, 2026-08): SEC extraction gaps leave some 10-K rows with None
    operating_cf/eps — strict "None = not proven" demoted MCD/GOOG/JPM/MA
    the day their partial filing landed, churning the book on DATA, not
    fundamentals. A reported NEGATIVE eps/cfo still demotes — this only
    bridges missing values, never reported ones. Point-in-time preserved:
    every value used was filed <= as_of.
    """
    conn = at_conn()
    try:
        cur = conn.execute(
            "SELECT period_end, filed, eps_diluted, operating_cf, "
            "shares_outstanding FROM annual WHERE ticker = ? AND filed <= ? "
            "ORDER BY filed DESC", (ticker.upper(), as_of))
        rows = cur.fetchall()
    finally:
        conn.close()
    if not rows:
        return None
    out = {"period_end": rows[0][0], "filed": rows[0][1],
           "eps_diluted": None, "operating_cf": None,
           "shares_outstanding": None}
    for period_end, filed, eps, cfo, shares in rows:
        if out["eps_diluted"] is None and eps is not None:
            out["eps_diluted"] = eps
        if out["operating_cf"] is None and cfo is not None:
            out["operating_cf"] = cfo
        if out["shares_outstanding"] is None and shares is not None:
            out["shares_outstanding"] = shares
        if all(v is not None for v in (out["eps_diluted"],
                                       out["operating_cf"],
                                       out["shares_outstanding"])):
            break
    return out


def price_on(ticker: str, as_of: str) -> Optional[float]:
    """Last close on/before as_of. None when no data."""
    conn = at_conn()
    try:
        cur = conn.execute(
            "SELECT close FROM prices WHERE ticker = ? AND date <= ? "
            "ORDER BY date DESC LIMIT 1", (ticker.upper(), as_of))
        row = cur.fetchone()
        return float(row[0]) if row else None
    finally:
        conn.close()


def closes_through(ticker: str, as_of: str, limit: int = 260) -> List[float]:
    """Daily closes ending on/before as_of, oldest-first (for the momentum window)."""
    conn = at_conn()
    try:
        cur = conn.execute(
            "SELECT close FROM prices WHERE ticker = ? AND date <= ? "
            "ORDER BY date DESC LIMIT ?", (ticker.upper(), as_of, limit))
        rows = [float(r[0]) for r in cur.fetchall()]
        rows.reverse()
        return rows
    finally:
        conn.close()


# ── Facts (per-ticker point-in-time snapshot for eligibility) ───────────
def snapshot_age_days(snap: dict, as_of: str) -> Optional[int]:
    try:
        return (datetime.strptime(as_of, "%Y-%m-%d")
                - datetime.strptime(snap["period_end"], "%Y-%m-%d")).days
    except (ValueError, TypeError):
        return None


def facts_for(ticker: str, as_of: str, in_sp500: bool,
              volume_waived: bool = False) -> Dict:
    """Point-in-time eligibility facts for one ticker (§3.1).

    Conservative rules (missing = not proven → Minor, never Major):
      - No snapshot, or snapshot > 730 days old (A_T staleness guard) →
        market cap AND quality floor are treated as unknown (not proven).
      - No price → market cap unknown.
      - No volume data and not waived → liquidity unknown.
    """
    snap = snapshot_metrics_on(ticker, as_of)
    price = price_on(ticker, as_of)
    facts = {
        "ticker": ticker.upper(),
        "in_sp500": in_sp500,
        "market_cap": None,
        "eps_ttm": None,
        "cfo_ttm": None,
        "avg_daily_volume": None,
        "snapshot_age_days": None,
    }
    if snap is not None:
        age = snapshot_age_days({"period_end": snap["period_end"]}, as_of)
        facts["snapshot_age_days"] = age
        if age is None or age <= 730:
            if price and snap.get("shares_outstanding"):
                facts["market_cap"] = price * snap["shares_outstanding"]
            facts["eps_ttm"] = snap.get("eps_diluted")
            facts["cfo_ttm"] = snap.get("operating_cf")
    if not volume_waived:
        facts["avg_daily_volume"] = store.avg_daily_volume(
            ticker, as_of, config.VOLUME_WINDOW_DAYS)
    return facts


def eligible(facts: Dict) -> bool:
    """meets_all_criteria over the pipeline facts dict."""
    return universe.meets_all_criteria(facts)


# ── Volume refresh (U3) ─────────────────────────────────────────────────
def fetch_volume_yfinance(ticker: str, days: int) -> List[tuple]:
    """[(date, volume)] via yfinance for the last `days` calendar days."""
    import yfinance as yf
    df = yf.Ticker(ticker).history(
        period=f"{days + 30}d", auto_adjust=False, actions=False)
    out = []
    for d, row in df.iterrows():
        vol = row.get("Volume")
        if vol is not None and float(vol) > 0:
            out.append((str(d)[:10], float(vol)))
    return out


def refresh_volumes(tickers: List[str], as_of: str,
                    fetch_fn: Callable[[str, int], List[tuple]] = fetch_volume_yfinance,
                    window_days: int = 0) -> Dict:
    """Fetch volume for tickers whose coverage is stale. Returns a summary.

    Systemic failure policy: if every fetch in the run raises (network down,
    yfinance outage), return systemic_failure=True and touch nothing — the
    caller waives U3 for this refresh instead of mass-demoting the book.
    Per-ticker failure is NOT systemic: that ticker simply has no volume
    (missing = not proven → Minor).
    """
    window_days = window_days or config.VOLUME_FETCH_WINDOW_DAYS
    stale = []
    for t in tickers:
        _min, _max, count = store.volume_coverage(t)
        if count == 0 or _max is None or _max < as_of:
            stale.append(t)
        else:
            try:
                if (datetime.strptime(as_of, "%Y-%m-%d")
                        - datetime.strptime(_max, "%Y-%m-%d")).days > config.VOLUME_STALE_DAYS:
                    stale.append(t)
            except ValueError:
                stale.append(t)
    if not stale:
        return {"checked": len(tickers), "fetched": 0, "failed": 0,
                "systemic_failure": False, "skipped": True}

    fetched = failed = 0
    ok_any = False
    for i, t in enumerate(stale):
        try:
            rows = fetch_fn(t, window_days)
            if rows:
                store.upsert_volume_many([(t, d, v) for d, v in rows])
                fetched += 1
                ok_any = True
            else:
                failed += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("volume fetch %s failed: %s", t, exc)
            failed += 1
    systemic = (not ok_any and len(stale) >= 3) or (not ok_any and len(stale) == len(tickers) > 0)
    return {"checked": len(tickers), "fetched": fetched, "failed": failed,
            "systemic_failure": systemic, "skipped": False}


# ── League orchestration (§3.2) ─────────────────────────────────────────
def update_leagues(facts_by_ticker: Dict[str, Dict], as_of: str) -> Dict:
    """Advance every tracked/eligible ticker's league state for one day.

    Orchestration lives in universe.apply_daily (shared with the walk-forward
    harness — ONE source of truth). This wrapper persists the resulting state
    to the store.
    """
    state = {r["ticker"]: r for r in store.all_leagues()}
    new_state, counts = universe.apply_daily(state, facts_by_ticker, as_of)
    for ticker, row in new_state.items():
        store.upsert_league(ticker, row["league"],
                            row["consecutive_compliant"],
                            row["consecutive_noncompliant"],
                            row["first_seen"], row["last_seen"])
    return counts


# ── Momentum + selection (§4) ───────────────────────────────────────────
def momentum_series(ticker: str, as_of: str) -> Optional[List[float]]:
    """Full close series ending at as_of (enough for 126/21)."""
    closes = closes_through(ticker, as_of, config.MOMENTUM_MIN_HISTORY + 30)
    if len(closes) < config.MOMENTUM_MIN_HISTORY:
        return None
    return closes


def run_selection(as_of: str, facts_by_ticker: Dict[str, Dict]) -> Dict:
    """Rank Major names, apply the quality veto, cap top-N, persist the feed.

    Returns the selection document (also saved to store + selection.json).
    """
    major = {r["ticker"] for r in store.all_leagues()
             if r["league"] == config.LEAGUE_MAJOR}
    prices = {}
    facts = {}
    for ticker in sorted(major):
        closes = momentum_series(ticker, as_of)
        if closes is None:
            continue  # insufficient history — can't rank (G2)
        prices[ticker] = closes
        facts[ticker] = facts_by_ticker.get(ticker, {})
    ranked = selector.rank_major(prices, facts, top_n=None)

    # All scored Major names (ranked desc) — /api/major returns these, not
    # just the top-N; the selection list is the NS-5 feed.
    scored_all = [{"ticker": r["ticker"], "momentum": r["momentum"]}
                  for r in ranked]
    # Anti-churn band (G5): keep prior picks that remain within the band.
    prev = store.latest_selection()
    held = {s["ticker"] for s in
            (prev or {}).get("payload", {}).get("selections", [])}
    ranked = selector.apply_turnover_band(ranked, held)

    doc = {
        "as_of": as_of,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "service": "NS-7",
        "methodology": "skip-month momentum 126/21 + quality veto + top-N",
        "major_count": len(major),
        "scored_count": len(prices),
        "top_n": config.TOP_N,
        "scores": scored_all,
        "selections": ranked,
    }
    store.save_selection(as_of, doc)
    config.SELECTION_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.SELECTION_PATH.write_text(json.dumps(doc, indent=2, default=str))
    return doc


# ── Full refresh ────────────────────────────────────────────────────────
def run_refresh(as_of: Optional[str] = None, fetch_volumes: bool = True,
                limit: int = 0) -> Dict:
    """One full pipeline pass. Returns a summary dict (also logged)."""
    as_of = as_of or datetime.now().strftime("%Y-%m-%d")
    store.init_db()

    sp500 = set(sp500_current())
    candidates = sorted(set(annual_tickers()) | sp500)
    if limit:
        candidates = candidates[:limit]

    volume = {"checked": 0, "fetched": 0, "failed": 0,
              "systemic_failure": False, "skipped": True}
    if fetch_volumes:
        volume = refresh_volumes(candidates, as_of)
    volume_waived = bool(volume.get("systemic_failure"))
    if volume_waived:
        store.set_meta("u3_waived", as_of)
        log.warning("U3 WAIVED for %s: systemic volume outage", as_of)

    facts_by_ticker = {}
    for t in candidates:
        facts_by_ticker[t] = facts_for(t, as_of, t in sp500,
                                       volume_waived=volume_waived)

    league = update_leagues(facts_by_ticker, as_of)

    selection = run_selection(as_of, facts_by_ticker)

    store.set_meta("last_refresh", as_of)
    summary = {
        "as_of": as_of,
        "candidates": len(candidates),
        "volume": volume,
        "volume_waived": volume_waived,
        "league": league,
        "selection": {k: selection[k] for k in ("as_of", "major_count",
                                                "scored_count", "top_n")},
        "top_picks": [s["ticker"] for s in selection["selections"][:10]],
    }
    log.info("refresh %s: %s", as_of, json.dumps(summary, default=str))
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="NS-7 daily pipeline refresh")
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: today)")
    ap.add_argument("--no-volume", action="store_true",
                    help="skip yfinance volume fetch (offline/test mode)")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap candidate universe (testing)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    summary = run_refresh(as_of=args.as_of, fetch_volumes=not args.no_volume,
                          limit=args.limit)
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
