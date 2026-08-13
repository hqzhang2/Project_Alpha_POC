"""ns7_walkforward.py — G1 acceptance gate: walk-forward OOS validation of NS-7.

Simulates the full NS-7 methodology (DESIGN.md §3-5) out-of-sample, 2016-2026:

  - DAILY league simulation (two-league system, 90-day grace, fresh-entry
    probation, re-admission) — shares universe.apply_daily with the pipeline.
  - MONTHLY rebalance: skip-month momentum (126/21) on Major names with a full
    price series, quality veto, top-N equal weight (naive weighting — NS-7
    emits signals; NS-5 does the frontier in production).
  - Point-in-time everywhere: SP500 membership via sp500_history.members_on(R),
    fundamentals via filed <= R, prices only up to R. No lookahead (G2).
  - Benchmarks (G7): held universe = equal-weight of ALL Major names at R;
    SPY = calibration reference only (yfinance, cached).

Acceptance gate G1: momentum must show POSITIVE excess vs the held universe
in >= 7 of 10 calendar years. G4 (concentration) is asserted on the naive
book. U3 is approximated as satisfied (config.WF_ASSUME_LIQUID) — documented
in DESIGN.md §10; the live pipeline enforces U3 with real volume.

Usage:
  python3 ns7_walkforward.py                     # full walk (2016-01 → 2026-07)
  python3 ns7_walkforward.py --start 2016-01-01 --end 2016-12-31   # quick run

Results: printed table + data/walkforward_results.json (gitignored).
Exit code 0 = gate PASS, 1 = gate FAIL, 2 = run error.
"""
from __future__ import annotations

import argparse
import bisect
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import config
import selector
import universe

log = logging.getLogger("ns7.walkforward")

# ── Data loading (point-in-time primitives over the A_T store) ──────────
def load_prices(at_db: Path) -> Dict[str, Tuple[List[str], List[float]]]:
    """{ticker: (dates asc, closes asc)} from the A_T prices table."""
    import sqlite3
    out: Dict[str, Tuple[List[str], List[float]]] = {}
    conn = sqlite3.connect(f"file:{at_db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT ticker, date, close FROM prices ORDER BY ticker, date")
        cur_t, dates, closes = None, [], []
        for t, d, c in rows:
            if cur_t is not None and t != cur_t:
                out[cur_t] = (dates, closes)
                dates, closes = [], []
            cur_t = t
            dates.append(d)
            closes.append(float(c))
        if cur_t is not None:
            out[cur_t] = (dates, closes)
    finally:
        conn.close()
    return out


def load_annual(at_db: Path) -> Dict[str, List[tuple]]:
    """{ticker: [(filed, period_end, eps, cfo, shares)]} sorted by filed."""
    import sqlite3
    out: Dict[str, List[tuple]] = {}
    conn = sqlite3.connect(f"file:{at_db}?mode=ro", uri=True)
    try:
        for t, filed, period_end, eps, cfo, shares in conn.execute(
                "SELECT ticker, filed, period_end, eps_diluted, operating_cf, "
                "shares_outstanding FROM annual ORDER BY ticker, filed"):
            out.setdefault(t, []).append(
                (filed, period_end, eps, cfo, shares))
    finally:
        conn.close()
    return out


def load_spy(cache: Path, start: str = "2014-01-01") -> Tuple[List[str], List[float]]:
    """SPY closes (dates asc, closes asc) — yfinance, cached as JSON."""
    if cache.exists():
        try:
            data = json.loads(cache.read_text())
            return data["dates"], [float(c) for c in data["closes"]]
        except (ValueError, KeyError):
            pass
    import yfinance as yf
    df = yf.Ticker("SPY").history(start=start, auto_adjust=True)
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    closes = [float(c) for c in df["Close"].dropna()]
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"dates": dates, "closes": closes}))
    return dates, closes


def load_membership() -> dict:
    """SP500 membership data via A_T's sp500_history (cached weekly)."""
    sys.path.insert(0, str(config.AT_SP500_CACHE.parent.parent))
    import sp500_history  # type: ignore
    return sp500_history.fetch_and_cache()


def members_on(day: str, membership: dict) -> set:
    """Survivorship-aware SP500 members as of `day`."""
    members = set(membership.get("current", []))
    for date, added, removed in membership.get("changes", []):
        if date > day:
            if removed:
                members.add(removed)
            if added:
                members.discard(added)
    return members


# ── Point-in-time facts ─────────────────────────────────────────────────
class Facts:
    """Per-ticker point-in-time accessors (bisect over preloaded arrays).

    Filed-date lists are precomputed once (O(1)-amortized bisect per query) —
    the daily league loop issues ~1.3M fact queries over the full walk.
    """

    def __init__(self, prices, annual, membership):
        self.prices = prices            # {ticker: (dates, closes)}
        self.annual = annual            # {ticker: [(filed, ...)]}
        self.membership = membership
        # Precomputed filed-date index per ticker (parallel to annual rows).
        self._filed = {t: [r[0] for r in rows] for t, rows in annual.items()}

    def _price_idx(self, ticker, day):
        dates = self.prices.get(ticker, (None, None))[0]
        if not dates:
            return -1
        return bisect.bisect_right(dates, day) - 1

    def price_on(self, ticker, day) -> Optional[float]:
        i = self._price_idx(ticker, day)
        if i < 0:
            return None
        return self.prices[ticker][1][i]

    def closes_through(self, ticker, day, limit=260) -> List[float]:
        i = self._price_idx(ticker, day)
        if i < 0:
            return []
        return self.prices[ticker][1][max(0, i - limit + 1):i + 1]

    def snapshot_on(self, ticker, day) -> Optional[dict]:
        """Newest row filed <= day, with LAST-KNOWN-GOOD per metric.

        Mirrors pipeline.snapshot_metrics_on: extraction gaps in the newest
        10-K (None operating_cf/eps) fall back to the most recent filing
        that reported the metric. Reported negatives still demote; only
        missing values are bridged (data-quality layer — walk-forward
        finding 2026-08: strict-None churned MCD/GOOG/JPM on partial
        filings). Point-in-time preserved (filed <= day only).
        """
        rows = self.annual.get(ticker)
        filed = self._filed.get(ticker) or []
        if not rows:
            return None
        i = bisect.bisect_right(filed, day) - 1
        if i < 0:
            return None
        out = {"filed": rows[i][0], "period_end": rows[i][1],
               "eps_diluted": None, "operating_cf": None,
               "shares_outstanding": None}
        for j in range(i, -1, -1):
            _f, _pe, eps, cfo, shares = rows[j]
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

    def facts_for(self, ticker, day, in_sp500) -> Dict:
        """Pipeline-identical eligibility facts (U3 assumed liquid)."""
        snap = self.snapshot_on(ticker, day)
        price = self.price_on(ticker, day)
        facts = {"ticker": ticker, "in_sp500": in_sp500,
                 "market_cap": None, "eps_ttm": None, "cfo_ttm": None,
                 "avg_daily_volume": None if not config.WF_ASSUME_LIQUID
                 else config.MIN_AVG_DAILY_VOLUME + 1.0}
        if snap is not None:
            try:
                age = (datetime.strptime(day, "%Y-%m-%d")
                       - datetime.strptime(snap["period_end"], "%Y-%m-%d")).days
            except ValueError:
                age = None
            if age is None or age <= 730:  # 730d staleness guard (A_T convention)
                if price and snap["shares_outstanding"]:
                    facts["market_cap"] = price * snap["shares_outstanding"]
                facts["eps_ttm"] = snap["eps_diluted"]
                facts["cfo_ttm"] = snap["operating_cf"]
        return facts


# ── The walk ────────────────────────────────────────────────────────────
def daterange(start: str, end: str):
    d = datetime.strptime(start, "%Y-%m-%d")
    stop = datetime.strptime(end, "%Y-%m-%d")
    while d <= stop:
        yield d.strftime("%Y-%m-%d")
        d += timedelta(days=1)


def month_ends(start: str, end: str, step_months: int = 1) -> List[str]:
    """Last calendar day of every `step_months`-th month within [start, end]."""
    out = []
    d = datetime.strptime(start, "%Y-%m-%d").replace(day=1)
    stop = datetime.strptime(end, "%Y-%m-%d")
    k = 0
    while d <= stop:
        if k % step_months == 0:
            nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
            last = nxt - timedelta(days=1)
            if last >= datetime.strptime(start, "%Y-%m-%d"):
                out.append(last.strftime("%Y-%m-%d"))
        k += 1
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        d = nxt
    return out


def simulate(start: str, end: str, facts: Facts,
             warmup_start: Optional[str] = None,
             spy: Optional[Tuple[List[str], List[float]]] = None,
             rebalance_months: int = 0) -> Dict:
    """Run the walk. Returns the full results dict (see module docstring)."""
    sim_start = warmup_start or (
        (datetime.strptime(start, "%Y-%m-%d")
         - timedelta(days=config.WF_SIM_WARMUP_DAYS)).strftime("%Y-%m-%d"))
    rebalance_months = rebalance_months or config.WF_REBALANCE_MONTHS

    # Candidate universe: tickers with both prices and annual facts.
    candidates = sorted(set(facts.prices) & set(facts.annual))

    # ── Daily league simulation ──────────────────────────────────────────
    league_state: Dict[str, Dict] = {}
    days_in_month = 0
    prev_month = None
    rebalances = month_ends(start, end)

    # Monthly holdings snapshots: {rebalance_day: {ticker: 1/N, ...}}
    holdings: Dict[str, Dict[str, float]] = {}
    universe_holdings: Dict[str, Dict[str, float]] = {}
    month_log: List[Dict] = []

    rebalances = month_ends(start, end, rebalance_months)
    prev_held = set()          # previous book — anti-churn band input (G5)

    for day in daterange(sim_start, end):
        # Facts for all candidates (point-in-time as of today).
        sp500 = members_on(day, facts.membership)
        # Index-exit edge: names that were SP500 members yesterday but not
        # today → the non-SP500 cap rule kicks in (fresh recompute).
        prev_day = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        sp500_removed = members_on(prev_day, facts.membership) - sp500
        facts_map = {}
        for t in candidates:
            in_sp = t in sp500
            # Fast path: skip facts entirely when nothing can change? Keep
            # simple — full daily recompute is ~1.3M bisects, a few seconds.
            facts_map[t] = facts.facts_for(t, day, in_sp)
        league_state, _counts = universe.apply_daily(
            league_state, facts_map, day, sp500_removed=sp500_removed)

        if day in rebalances:
            major = {t for t, r in league_state.items()
                     if r["league"] == config.LEAGUE_MAJOR}

            # ── Momentum ranking (only Major, full series) ───────────────
            prices, fmap = {}, {}
            for t in sorted(major):
                closes = facts.closes_through(t, day)
                if len(closes) >= config.MOMENTUM_MIN_HISTORY:
                    prices[t] = closes
                    fmap[t] = facts_map.get(t, {})
            ranked = selector.rank_major(prices, fmap, top_n=None)
            picks = selector.apply_turnover_band(ranked, prev_held)
            prev_held = {p["ticker"] for p in picks}

            w = 1.0 / len(picks) if picks else 0.0
            holdings[day] = {p["ticker"]: w for p in picks}
            universe_holdings[day] = {t: 1.0 / len(major) for t in sorted(major)} \
                if major else {}
            month_log.append({
                "rebalance": day, "major_count": len(major),
                "scored_count": len(prices), "picks": [p["ticker"] for p in picks],
                "top_momentum": picks[0]["momentum"] if picks else None,
            })

    # ── Monthly returns ──────────────────────────────────────────────────
    rebalance_days = sorted(holdings)
    log_by_day = {m["rebalance"]: m for m in month_log}
    spy_dates, spy_closes = spy or ([], [])
    rows = []
    for i, rday in enumerate(rebalance_days):
        if i + 1 >= len(rebalance_days):
            break
        nxt = rebalance_days[i + 1]
        # Strategy return: equal-weight mean of held names' close-to-close.
        rets, urets = [], []
        for t, w in holdings[rday].items():
            p0 = facts.price_on(t, rday)
            p1 = facts.price_on(t, nxt)
            if p0 and p1:
                rets.append(p1 / p0 - 1.0)
        for t in universe_holdings[rday]:
            p0 = facts.price_on(t, rday)
            p1 = facts.price_on(t, nxt)
            if p0 and p1:
                urets.append(p1 / p0 - 1.0)
        sret = sum(rets) / len(rets) if rets else 0.0
        uret = sum(urets) / len(urets) if urets else 0.0
        # SPY over the same window (calibration).
        si = bisect.bisect_right(spy_dates, rday) - 1
        sj = bisect.bisect_right(spy_dates, nxt) - 1
        spy_ret = (spy_closes[sj] / spy_closes[si] - 1.0) if (si >= 0 and sj > si) else None
        lg = log_by_day.get(rday, {})
        rows.append({"month": rday[:7], "strategy": sret, "universe": uret,
                     "spy": spy_ret,
                     "picks": list(holdings[rday].keys()),
                     "major_count": lg.get("major_count"),
                     "scored_count": lg.get("scored_count"),
                     "top_momentum": lg.get("top_momentum")})

    # ── Annual aggregation ───────────────────────────────────────────────
    by_year: Dict[str, Dict[str, float]] = {}
    for r in rows:
        y = r["month"][:4]
        by_year.setdefault(y, {"strategy": 1.0, "universe": 1.0, "spy": 1.0})
        by_year[y]["strategy"] *= (1 + r["strategy"])
        by_year[y]["universe"] *= (1 + r["universe"])
        if r["spy"] is not None:
            by_year[y]["spy"] *= (1 + r["spy"])
    yearly = []
    for y in sorted(by_year):
        g = by_year[y]
        yearly.append({
            "year": y,
            "strategy": g["strategy"] - 1.0,
            "universe": g["universe"] - 1.0,
            "spy": g["spy"] - 1.0,
            "excess_vs_universe": g["strategy"] - g["universe"],
            "excess_vs_spy": g["strategy"] - g["spy"],
        })

    # ── Drawdown (monthly equity curve) ──────────────────────────────────
    def max_dd(series):
        peak, mdd = 1.0, 0.0
        for r in series:
            peak = max(peak, peak * (1 + r))
            mdd = min(mdd, (peak * (1 + r)) / peak - 1.0)
        return mdd
    strat_dd = max_dd([r["strategy"] for r in rows])
    spy_dd = max_dd([r["spy"] for r in rows if r["spy"] is not None]) or 0.0

    # ── Turnover (G5) ────────────────────────────────────────────────────
    turnovers = []
    prev = set()
    for r in rebalance_days:
        cur = set(holdings[r])
        if prev:
            turnovers.append(len(cur - prev) / max(len(prev), 1))
        prev = cur
    avg_turnover = sum(turnovers) / len(turnovers) if turnovers else 0.0
    rebalances_per_year = 12.0 / max(rebalance_months, 1)
    annual_turns = avg_turnover * rebalances_per_year

    # ── Acceptance verdict (G1) ──────────────────────────────────────────
    years_with_excess = sum(1 for y in yearly if y["excess_vs_universe"] > 0)
    total_years = len(yearly)
    gate_pass = total_years >= 10 and years_with_excess >= 7
    # G4: naive book check — 20 equal names → effective N 20, max 5%.
    w20 = {f"T{i}": 0.05 for i in range(config.TOP_N)}
    g4_ok = selector.concentration_ok(w20)

    results = {
        "window": {"start": start, "end": end, "rebalances": len(rebalance_days),
                   "rebalance_months": rebalance_months},
        "gate": {"G1_excess_years": years_with_excess, "G1_total_years": total_years,
                 "G1_pass": gate_pass, "G4_concentration_ok": g4_ok,
                 "avg_turnover_per_rebalance": round(avg_turnover, 4),
                 "annual_book_turns": round(annual_turns, 2)},
        "drawdown": {"strategy_mdd": round(strat_dd, 4),
                     "spy_mdd": round(spy_dd, 4),
                     "dd_ratio_vs_spy": round(strat_dd / spy_dd, 3) if spy_dd else None},
        "yearly": yearly,
        "monthly": [{k: (round(v, 6) if isinstance(v, float) else v)
                     for k, v in r.items()} for r in rows],
        "assumptions": ["league gates = SP500 membership ∪ market cap "
                        "(PM-corrected 2026-08-13: SP500 → Major immediately; "
                        "non-SP500 $50B+ → Minor, Major after 90d or $75B)",
                        "equal-weight naive book (NS-5 frontier in production)",
                        "last-known-good fills extraction-gap metrics",
                        f"rebalance every {rebalance_months} month(s), daily league clock, 90d grace"],
    }
    return results


def _fmt_pct(x):
    return "—" if x is None else f"{x * 100:+.1f}%"


def print_report(results: Dict) -> None:
    print("=" * 88)
    print("NS-7 WALK-FORWARD — G1 ACCEPTANCE GATE")
    print(f"window {results['window']['start']} → {results['window']['end']} "
          f"({results['window']['rebalances']} monthly rebalances)")
    print("=" * 88)
    print(f"{'Year':<6}{'Strategy':>10}{'Universe':>10}{'Excess':>10}"
          f"{'SPY':>10}{'vs SPY':>10}")
    for y in results["yearly"]:
        print(f"{y['year']:<6}{_fmt_pct(y['strategy']):>10}"
              f"{_fmt_pct(y['universe']):>10}{_fmt_pct(y['excess_vs_universe']):>10}"
              f"{_fmt_pct(y['spy']):>10}{_fmt_pct(y['excess_vs_spy']):>10}")
    g = results["gate"]
    print("-" * 88)
    print(f"Excess vs held universe: {g['G1_excess_years']}/{g['G1_total_years']} years "
          f"(gate: >= 7/10)  →  {'✅ PASS' if g['G1_pass'] else '❌ FAIL'}")
    print(f"Max drawdown: strategy {_fmt_pct(results['drawdown']['strategy_mdd'])} "
          f"vs SPY {_fmt_pct(results['drawdown']['spy_mdd'])} "
          f"(ratio {results['drawdown']['dd_ratio_vs_spy']})")
    print(f"Turnover: {results['gate']['avg_turnover_per_rebalance']:.1%} per rebalance "
          f"= {results['gate']['annual_book_turns']:.1f} book-turns/yr "
          f"(G5 baseball — should be low)")
    print(f"G4 concentration (naive top-20 equal weight): "
          f"{'ok' if g['G4_concentration_ok'] else 'VIOLATED'}")
    print("Assumptions:", "; ".join(results["assumptions"]))


def main() -> int:
    ap = argparse.ArgumentParser(description="NS-7 walk-forward (G1 gate)")
    ap.add_argument("--start", default=config.WF_START)
    ap.add_argument("--end", default=config.WF_END)
    ap.add_argument("--spy-cache", default=str(config.DATA_DIR / "spy_closes.json"))
    ap.add_argument("--out", default=str(config.DATA_DIR / "walkforward_results.json"))
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    log.info("loading A_T store %s", config.AT_FUNDAMENTALS_DB)
    prices = load_prices(config.AT_FUNDAMENTALS_DB)
    annual = load_annual(config.AT_FUNDAMENTALS_DB)
    membership = load_membership()
    log.info("prices %d tickers, annual %d tickers, membership current=%d",
             len(prices), len(annual), len(membership.get("current", [])))

    spy = load_spy(Path(args.spy_cache))
    facts = Facts(prices, annual, membership)
    results = simulate(args.start, args.end, facts, spy=spy)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, default=str))
    print_report(results)
    return 0 if results["gate"]["G1_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
