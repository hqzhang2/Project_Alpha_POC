"""d1_wf_compare.py — v4.6 weighting-scheme comparison harness (DPF).

Replays the NS-7 walk-forward ONCE, capturing each rebalance's ranked picks
with their momentum/vol/rank/tenure inputs, then applies every weighting
scheme to the SAME books and reports per-scheme CAGR / MaxDD / DD-ratio-vs-SPY
/ excess-years. This isolates the weighting decision exactly as designed
(research_d1_basket_v46.md §9): monotonic transforms of the same validated
selection — no re-selection between schemes.

Schemes: momentum_score (default), rank_tilted, risk_normalized,
tenure_aware, plus the equal-weight baseline.

Usage (CLT py3.9 runtime):
  python3 d1_wf_compare.py [--start ... --end ... --out ...]
Output: printed table + data/d1_wf_compare.json (gitignored).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import config
import d1_basket as d1b
import ns7_walkforward as wf

log = logging.getLogger("ns7.d1_wf_compare")


# ── Scheme application on a captured book ────────────────────────────────
def apply_scheme(scheme: str,
                 picks: List[dict],
                 vol_by_ticker: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    """Weight one captured book per scheme; normalized to sum 1.0.

    picks = [{ticker, momentum, rank}, ...] ascending rank (the G5 band has
    already been applied by the walk). vol_by_ticker supplies ex-ante sigma.
    """
    if not picks:
        return {}
    tickers = [p["ticker"] for p in picks]

    if scheme in ("equal_weight", "momentum_score", "rank_tilted",
                  "risk_normalized", "tenure_aware"):
        pass  # tenure_aware = momentum_score base + overlay applied by caller
    else:
        raise ValueError(f"unknown scheme '{scheme}'")

    if scheme == "equal_weight":
        w = {t: 1.0 for t in tickers}
    elif scheme == "tenure_aware":
        # must match the shipped d1_basket.weight_basket('tenure_aware'):
        # a momentum_score base, then the recency overlay.
        w = apply_scheme("momentum_score", picks, vol_by_ticker)
    elif scheme == "momentum_score":
        raw = {p["ticker"]: max(float(p.get("momentum", 0.0)), 0.0) for p in picks}
        if sum(raw.values()) <= 0:
            w = {t: 1.0 for t in tickers}
        else:
            w = raw
    elif scheme == "rank_tilted":
        n = len(picks)
        if getattr(config, "D1_RANK_TILT_GEOMETRIC", False):
            w = {p["ticker"]: 2.0 ** (-float(p["rank"])) for p in picks}
        else:
            w = {p["ticker"]: float(n + 1 - p["rank"]) for p in picks}
    elif scheme == "risk_normalized":
        usable = {t: v for t, v in (vol_by_ticker or {}).items()
                  if v and v > 0}
        if len(usable) < len(tickers):
            # missing vol for any name → equal weight over names with vol
            usable = {t: 1.0 for t in tickers} if not usable else usable
        w = {t: 1.0 / usable[t] for t in usable}
    else:
        raise ValueError(f"unknown scheme '{scheme}'")

    total = sum(w.values())
    return {t: x / total for t, x in w.items()} if total > 0 else {}


def tenure_aware_overlay(weights: Dict[str, float],
                         tenure_days_at_rebalance: Dict[str, Optional[int]]) -> Dict[str, float]:
    """Apply the recency decay to already-normalized weights, renormalize."""
    fresh = config.D1_TENURE_FRESH_DAYS
    long_tooth = config.D1_TENURE_LONG_TOOTH_DAYS
    min_f = config.D1_TENURE_MIN_FACTOR
    out = {}
    for t, w in weights.items():
        dd = tenure_days_at_rebalance.get(t)
        if dd is None:
            f = 1.0
        elif dd <= fresh:
            f = 1.0
        elif dd >= long_tooth:
            f = min_f
        else:
            f = 1.0 - (dd - fresh) / (long_tooth - fresh) * (1.0 - min_f)
        out[t] = w * f
    total = sum(out.values())
    return {t: v / total for t, v in out.items()} if total > 0 else {}


# ── Metrics ──────────────────────────────────────────────────────────────
def metrics(monthly: List[Dict]) -> Dict:
    """CAGR, MaxDD, from a [{month, ret}] series (rebalance-period returns)."""
    eq = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in monthly:
        eq *= (1.0 + r["ret"])
        peak = max(peak, eq)
        max_dd = min(max_dd, (eq / peak) - 1.0)
    years = len(monthly) / 12.0
    cagr = (eq ** (1.0 / years) - 1.0) if years > 0 and eq > 0 else None
    return {"cagr": cagr, "max_dd": round(max_dd, 4), "total_return": round(eq - 1, 4)}


# ── The comparison ───────────────────────────────────────────────────────
SCHEMES = ["equal_weight", "momentum_score", "rank_tilted", "risk_normalized",
           "tenure_aware"]


def compare(start: str, end: str) -> Dict:
    """Walk once with instrumented book capture; evaluate all schemes."""
    log.info("loading A_T store %s", config.AT_FUNDAMENTALS_DB)
    prices = wf.load_prices(config.AT_FUNDAMENTALS_DB)
    annual = wf.load_annual(config.AT_FUNDAMENTALS_DB)
    membership = wf.load_membership()
    spy = wf.load_spy(Path(getattr(config, "DATA_DIR", Path("data"))
                           / "spy_closes.json"), start=start)

    facts = wf.Facts(prices, annual, membership)
    sim_start = ((datetime.strptime(start, "%Y-%m-%d")
                  - timedelta(days=config.WF_SIM_WARMUP_DAYS)).strftime("%Y-%m-%d"))

    candidates = sorted(set(facts.prices) & set(facts.annual))
    league_state: Dict[str, Dict] = {}
    prev_held = set()
    holdings_books: Dict[str, List[dict]] = {}     # rebalance day → ranked picks
    universe_holdings: Dict[str, Dict[str, float]] = {}

    rebalances = wf.month_ends(start, end, config.WF_REBALANCE_MONTHS)

    day = datetime.strptime(sim_start, "%Y-%m-%d")
    stop = datetime.strptime(end, "%Y-%m-%d")
    from datetime import timedelta as _td
    while day <= stop:
        ds = day.strftime("%Y-%m-%d")
        sp500 = wf.members_on(ds, facts.membership)
        prev_ds = (day - _td(days=1)).strftime("%Y-%m-%d")
        sp500_removed = wf.members_on(prev_ds, facts.membership) - sp500

        facts_map = {}
        for t in candidates:
            in_sp = t in sp500
            facts_map[t] = facts.facts_for(t, ds, in_sp)
        league_state, _counts = wf.universe.apply_daily(
            league_state, facts_map, ds, sp500_removed=sp500_removed)

        if ds in rebalances:
            major = {t for t, r in league_state.items()
                     if r["league"] == config.LEAGUE_MAJOR}
            prices_now, fmap = {}, {}
            for t in sorted(major):
                closes = facts.closes_through(t, ds)
                if len(closes) >= config.MOMENTUM_MIN_HISTORY:
                    prices_now[t] = closes
                    fmap[t] = facts_map.get(t, {})
            ranked = wf.selector.rank_major(prices_now, fmap, top_n=None)
            picks = wf.selector.apply_turnover_band(ranked, prev_held)[:config.BASKET_TOP_N]
            prev_held = {p["ticker"] for p in picks}

            holdings_books[ds] = picks
            universe_holdings[ds] = ({t: 1.0 / len(major) for t in sorted(major)}
                                     if major else {})
        day += _td(days=1)

    # ── Period returns per scheme ────────────────────────────────────────
    reb_days = sorted(holdings_books)
    spy_dates, spy_closes = spy or ([], [])
    import bisect
    per_scheme_monthly = {s: [] for s in SCHEMES}
    spy_monthly = []
    for i, rday in enumerate(reb_days):
        if i + 1 >= len(reb_days):
            break
        nxt = reb_days[i + 1]
        picks = holdings_books[rday]

        # ex-ante vol per name over the prior window (for risk_normalized)
        vol = {}
        for p in picks:
            cl = facts.closes_through(p["ticker"], rday)
            tail = cl[-61:]
            rets = [tail[j] / tail[j - 1] - 1.0 for j in range(1, len(tail))
                    if tail[j - 1]]
            if len(rets) >= 20:
                mu = sum(rets) / len(rets)
                vol[p["ticker"]] = (sum((x - mu) ** 2 for x in rets)
                                    / len(rets)) ** 0.5

        # period price returns per held name
        pret: Dict[str, float] = {}
        for p in picks:
            p0 = facts.price_on(p["ticker"], rday)
            p1 = facts.price_on(p["ticker"], nxt)
            if p0 and p1:
                pret[p["ticker"]] = p1 / p0 - 1.0

        si = bisect.bisect_right(spy_dates, rday) - 1
        sj = bisect.bisect_right(spy_dates, nxt) - 1
        spy_ret = ((spy_closes[sj] / spy_closes[si] - 1.0)
                   if (si >= 0 and sj > si) else None)

        for s in SCHEMES:
            w = apply_scheme(s, picks, vol)
            if s == "tenure_aware":
                # tenure is live-state only since go-live; historical replay has
                # none — neutral overlay (documents itself as such in output)
                ten: Dict[str, Optional[int]] = {t: None for t in w}
                w = tenure_aware_overlay(w, ten)
            rets = [pret[t] * w[t] for t in w if t in pret]
            covered = sum(w[t] for t in w if t in pret)
            mret = (sum(rets) / covered) if covered > 0 else 0.0
            per_scheme_monthly[s].append({"month": rday[:7], "ret": mret})
        if spy_ret is not None:
            spy_monthly.append({"month": rday[:7], "ret": spy_ret})

    # ── Aggregate ────────────────────────────────────────────────────────
    results = {"window": {"start": start, "end": end,
                          "rebalances": len(reb_days)}, "schemes": {}}
    spy_m = metrics(spy_monthly)
    for s in SCHEMES:
        m = metrics(per_scheme_monthly[s])
        excess_years = 0
        total_years = 0
        by_year: Dict[str, Dict[str, float]] = {}
        for r, sr in zip(per_scheme_monthly[s], spy_monthly):
            y = r["month"][:4]
            by_year.setdefault(y, {"s": 1.0, "spy": 1.0})
            by_year[y]["s"] *= (1 + r["ret"])
            if sr["ret"] is not None:
                by_year[y]["spy"] *= (1 + sr["ret"])
        for y in sorted(by_year):
            total_years += 1
            if by_year[y]["s"] - 1.0 > by_year[y]["spy"] - 1.0:
                excess_years += 1
        results["schemes"][s] = {
            **m, "excess_years_vs_spy": f"{excess_years}/{total_years}",
            "yearly": {y: {"ret": round(v["s"] - 1, 4),
                           "excess": round(v["s"] - v["spy"], 4)}
                       for y, v in sorted(by_year.items())},
        }
    results["spy"] = spy_m
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="D1 weighting-scheme comparison")
    ap.add_argument("--start", default=config.WF_START)
    ap.add_argument("--end", default=config.WF_END)
    ap.add_argument("--out", default=str(config.DATA_DIR / "d1_wf_compare.json"))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    res = compare(args.start, args.end)
    Path(args.out).write_text(json.dumps(res, indent=2))

    print(f"\nD1 weighting-scheme comparison {args.start} → {args.end} "
          f"({res['window']['rebalances']} rebalances)\n")
    print(f"{'Scheme':<18}{'CAGR':>8}{'MaxDD':>9}{'Excess vs SPY':>16}")
    for s in SCHEMES:
        m = res["schemes"][s]
        cagr = f"{m['cagr']:.2%}" if m["cagr"] is not None else "—"
        print(f"{s:<18}{cagr:>8}{m['max_dd']:>9.2%}{m['excess_years_vs_spy']:>16}")
    sm = res["spy"]
    print(f"{'SPY (ref)':<18}{sm['cagr']:.2%}" if sm["cagr"] is not None
          else f"{'SPY (ref)':<18}{'—':>8}")
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
