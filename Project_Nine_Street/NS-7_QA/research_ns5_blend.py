#!/usr/bin/env python3
"""research_ns5_blend.py — 2a: two-sleeve blend walk-forward.

Simulates DESIGN §4.3's joint universe BEFORE touching NS-5: the momentum
sleeve (NS-7 top-N, existing walk-forward machinery) plus the value sleeve
(A_T 4-framework ensemble, agreement ≥ 2, replayed point-in-time via
fundamental_screener.screen_universe), sized by a GDP×CPI regime tilt
(FRED, cached to data/macro_hist.json).

Measures (full-stack G7 targets):
  - max drawdown vs SPY          (mandate: ≤ 0.5× SPY for the FULL stack)
  - yearly excess vs held universe (G1-style) and vs SPY
  - book turnover + sleeve/regime breakdown

Assumptions (PM review items, flagged in the findings doc):
  A1 tilt mapping: growth (GDP YoY ≥ 1.5% AND CPI YoY ≤ 3.0%) → 70/30
     momentum/value; defensive → 30/70. A step tilt, not NS-5's frontier.
  A2 value sleeve = top-20 by agreement (A_T agreement ≥ 2 standard),
     equal-weight — a candidate-pool proxy; NS-5's frontier does the real
     sizing in 2b.
  A3 FRED observations as-revised (minor lookahead — house macro already
     accepts this).
  A4 no transaction costs.

Run (house runtime):
  /Library/Developer/CommandLineTools/.../3.9/bin/python3 research_ns5_blend.py
Results: data/blend_results.json + research_ns5_blend.md (both gitignored).
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
AT_DIR = Path("/Users/chuck/Project_Alpha_POC/Project_Sequoia/terminal")

# NS-7 modules FIRST — A_T's terminal also has a config.py; its modules
# never import it, but OUR `import config` must resolve to NS-7's.
import config  # noqa: E402  (NS-7)
import ns7_walkforward as wf  # noqa: E402
import selector  # noqa: E402
import universe  # noqa: E402

sys.path.insert(0, str(AT_DIR))
import fundamental_screener  # noqa: E402  (A_T — value sleeve, read-only)
import macro as at_macro  # noqa: E402  (A_T — FRED key, read-only)

MACRO_CACHE = config.DATA_DIR / "macro_hist.json"
BLEND_RESULTS = config.DATA_DIR / "blend_results.json"
FINDINGS = Path("/Users/chuck/Project_Alpha_POC/Project_Nine_Street") / "research_ns5_blend.md"

# Regime thresholds + tilt (assumption A1 — PM review item)
GDP_MIN_YOY = 0.015   # real GDP YoY ≥ 1.5% → growth-ish
CPI_MAX_YOY = 0.030   # CPI YoY ≤ 3.0% → growth-ish
TILT = {"growth": (0.70, 0.30), "defensive": (0.30, 0.70)}
TILT_MODES = ("regime", "momentum", "value")   # PM decision evidence (both sleeves)
VALUE_SLEEVE_N = 20


# ── FRED macro (GDP YoY, CPI YoY) — full history, cached ────────────────
def _fred_observations(series_id: str, units: str = "") -> dict:
    key = at_macro._fred_key()
    url = (f"{at_macro.FRED_OBS_URL}?series_id={series_id}&api_key={key}"
           f"&file_type=json&observation_start=2014-01-01"
           + (f"&units={units}" if units else ""))
    with urllib.request.urlopen(url, timeout=20) as r:
        obs = json.loads(r.read().decode())
    return {o["date"]: float(o["value"]) for o in obs["observations"] if o["value"] != "."}


def _load_macro() -> dict:
    """{gdp_yoy: {date: pct}, cpi_yoy: {date: pct}} — FRED, cached."""
    if MACRO_CACHE.exists():
        return json.loads(MACRO_CACHE.read_text())
    gdp_level = _fred_observations("GDPC1")            # quarterly level
    cpi_yoy = _fred_observations("CPIAUCSL", "pc1")    # CPI YoY, PERCENT
    gdp_yoy = {}
    dates = sorted(gdp_level)
    for i in range(4, len(dates)):
        gdp_yoy[dates[i]] = round(
            float(gdp_level[dates[i]]) / float(gdp_level[dates[i - 4]]) - 1.0, 6)
    # Normalize both to FRACTIONS (FRED pc1 is percent; GDP YoY is computed).
    cpi_yoy = {d: round(v / 100.0, 6) for d, v in cpi_yoy.items()}
    out = {"gdp_yoy": gdp_yoy, "cpi_yoy": cpi_yoy}
    MACRO_CACHE.parent.mkdir(parents=True, exist_ok=True)
    MACRO_CACHE.write_text(json.dumps(out))
    return out


def regime_on(day: str, macro: dict) -> str:
    """Latest GDP/CPI observation ≤ day → 'growth' | 'defensive' (A1)."""
    g, c = macro["gdp_yoy"], macro["cpi_yoy"]
    gd, cd = sorted(g), sorted(c)
    gi = bisect.bisect_right(gd, day) - 1
    ci = bisect.bisect_right(cd, day) - 1
    if gi < 0 or ci < 0:
        return "defensive"   # no data yet → defensive default
    gdp = float(g[gd[gi]])
    cpi = float(c[cd[ci]])
    return "growth" if (gdp >= GDP_MIN_YOY and cpi <= CPI_MAX_YOY) else "defensive"


# ── Value sleeve replay (A_T validated scorer, point-in-time) ────────────
def value_sleeve(day: str) -> list:
    """Top-N A_T agreement picks as of `day` (agreement ≥ 2), ranked."""
    rows = fundamental_screener.screen_universe(as_of=day)
    return [r["ticker"] for r in rows if r["agreement"] >= 2][:VALUE_SLEEVE_N]


# ── Blend walk-forward ───────────────────────────────────────────────────
def run_blend(start: str, end: str, facts: wf.Facts, macro: dict,
              spy: tuple, rebalance_months: int = 3,
              tilt_mode: str = "regime") -> dict:
    """Walk with one sleeve policy: 'regime' (GDP×CPI tilt), 'momentum'
    (100% growth sleeve), or 'value' (100% defensive sleeve) — the three
    series the PM needs to decide the joint-universe policy."""
    sim_start = (datetime.strptime(start, "%Y-%m-%d")
                 - timedelta(days=config.WF_SIM_WARMUP_DAYS)).strftime("%Y-%m-%d")
    candidates = sorted(set(facts.prices) & set(facts.annual))
    rebalances = wf.month_ends(start, end, rebalance_months)

    league_state = {}
    holdings: dict = {}            # {rebalance_day: {ticker: weight}}
    universe_holdings: dict = {}
    month_log = []
    prev_held = set()

    for day in wf.daterange(sim_start, end):
        sp500 = wf.members_on(day, facts.membership)
        prev_day = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        sp500_removed = wf.members_on(prev_day, facts.membership) - sp500
        facts_map = {t: facts.facts_for(t, day, t in sp500) for t in candidates}
        league_state, _ = universe.apply_daily(
            league_state, facts_map, day, sp500_removed=sp500_removed)

        if day in rebalances:
            major = {t for t, r in league_state.items()
                     if r["league"] == config.LEAGUE_MAJOR}
            # Momentum sleeve (existing NS-7 logic)
            prices, fmap = {}, {}
            for t in sorted(major):
                closes = facts.closes_through(t, day)
                if len(closes) >= config.MOMENTUM_MIN_HISTORY:
                    prices[t] = closes
                    fmap[t] = facts_map.get(t, {})
            ranked = selector.rank_major(prices, fmap, top_n=None)
            mom_picks = selector.apply_turnover_band(ranked, prev_held)
            prev_held = {p["ticker"] for p in mom_picks}
            # Value sleeve (A_T ensemble replay) — skip when momentum-only.
            val_picks = value_sleeve(day) if tilt_mode != "momentum" else []
            # Sleeve policy: regime tilt, or a pure sleeve (PM evidence).
            regime = regime_on(day, macro)
            if tilt_mode == "regime":
                w_mom, w_val = TILT[regime]
            elif tilt_mode == "momentum":
                w_mom, w_val = 1.0, 0.0
            else:
                w_mom, w_val = 0.0, 1.0
            book = {}
            if mom_picks:
                wm = w_mom / len(mom_picks)
                book.update({p["ticker"]: wm for p in mom_picks})
            if val_picks:
                wv = w_val / len(val_picks)
                book.update({t: wv for t in val_picks})
            holdings[day] = book
            universe_holdings[day] = {t: 1.0 / len(major) for t in sorted(major)} if major else {}
            month_log.append({
                "rebalance": day, "regime": regime, "w_mom": w_mom,
                "mom_picks": [p["ticker"] for p in mom_picks],
                "val_picks": val_picks, "major_count": len(major),
            })

    # ── Monthly returns (weighted by the blended book) ──────────────────
    rebalance_days = sorted(holdings)
    log_by_day = {m["rebalance"]: m for m in month_log}
    spy_dates, spy_closes = spy or ([], [])
    rows = []
    for i, rday in enumerate(rebalance_days):
        if i + 1 >= len(rebalance_days):
            break
        nxt = rebalance_days[i + 1]
        sret, urets = 0.0, []
        for t, w in holdings[rday].items():
            p0, p1 = facts.price_on(t, rday), facts.price_on(t, nxt)
            if p0 and p1:
                sret += w * (p1 / p0 - 1.0)
        for t in universe_holdings[rday]:
            p0, p1 = facts.price_on(t, rday), facts.price_on(t, nxt)
            if p0 and p1:
                urets.append(p1 / p0 - 1.0)
        uret = sum(urets) / len(urets) if urets else 0.0
        si = bisect.bisect_right(spy_dates, rday) - 1
        sj = bisect.bisect_right(spy_dates, nxt) - 1
        spy_ret = (spy_closes[sj] / spy_closes[si] - 1.0) if (si >= 0 and sj > si) else None
        lg = log_by_day.get(rday, {})
        rows.append({"month": rday[:7], "strategy": sret, "universe": uret,
                     "spy": spy_ret, "regime": lg.get("regime"),
                     "w_mom": lg.get("w_mom"),
                     "mom": list(holdings[rday].keys())})

    # ── Annual aggregation ──────────────────────────────────────────────
    by_year = {}
    for r in rows:
        y = r["month"][:4]
        by_year.setdefault(y, {"strategy": 1.0, "universe": 1.0, "spy": 1.0})
        by_year[y]["strategy"] *= (1 + r["strategy"])
        by_year[y]["universe"] *= (1 + r["universe"])
        if r["spy"] is not None:
            by_year[y]["spy"] *= (1 + r["spy"])
    yearly = [{"year": y, "strategy": g["strategy"] - 1.0,
               "universe": g["universe"] - 1.0, "spy": g["spy"] - 1.0,
               "excess_vs_universe": g["strategy"] - g["universe"],
               "excess_vs_spy": g["strategy"] - g["spy"]}
              for y, g in sorted(by_year.items())]

    # ── Drawdown + turnover + regime stats ──────────────────────────────
    def max_dd(curve: list) -> float:
        peak, mdd = 1.0, 0.0
        for v in curve:
            peak = max(peak, v)
            mdd = min(mdd, v / peak - 1.0)
        return mdd

    strat_curve, spy_curve = [1.0], [1.0]
    for r in rows:
        strat_curve.append(strat_curve[-1] * (1 + r["strategy"]))
        spy_curve.append(spy_curve[-1] * (1 + (r["spy"] or 0.0)))
    dd_strat, dd_spy = max_dd(strat_curve), max_dd(spy_curve)

    book_turns = 0.0
    for a, b in zip(rebalance_days, rebalance_days[1:]):
        ka, kb = set(holdings[a]), set(holdings[b])
        if ka:
            book_turns += len(ka - kb) / len(ka)
    # Annualize by ACTUAL years (rows are rebalance-to-rebalance intervals
    # of rebalance_months each — dividing by len(rows)/12 would inflate ~3×).
    years = len(rows) * rebalance_months / 12.0
    turns_per_year = book_turns / years if years else 0.0

    regimes = {}
    for m in month_log:
        regimes.setdefault(m["regime"], {"n": 0, "w_mom_sum": 0.0})
        regimes[m["regime"]]["n"] += 1
        regimes[m["regime"]]["w_mom_sum"] += m["w_mom"]

    excess_ok = sum(1 for y in yearly if y["excess_vs_universe"] > 0)
    return {"window": {"start": start, "end": end, "rebalances": len(rows)},
            "yearly": yearly, "excess_years": excess_ok,
            "rows": rows,   # per-rebalance-interval rows (R1 combined harness)
            "drawdown": {"strategy": dd_strat, "spy": dd_spy,
                         "ratio": abs(dd_strat / dd_spy) if dd_spy else None},
            "turnover": {"fraction_per_rebalance": book_turns / len(rows) if rows else 0,
                         "turns_per_year": turns_per_year},
            "regimes": {k: {"n": v["n"], "avg_w_mom": v["w_mom_sum"] / v["n"]}
                        for k, v in regimes.items()},
            "sleeve": {"mom_names": len(month_log[-1]["mom_picks"]) if month_log else 0,
                       "val_names": len(month_log[-1]["val_picks"]) if month_log else 0}}


def write_findings(res_by_mode: dict, window: dict) -> None:
    """Comparison findings — momentum-only vs value-only vs regime blend."""
    lines = [
        "# NS-5 Blend Research (2a) — sleeve decision evidence (PM)",
        "",
        f"Run: {datetime.now():%Y-%m-%d %H:%M} · window {window['start']} → {window['end']} · quarterly rebalance",
        "",
        "Three policies, same machinery (NS-7 momentum sleeve ∪ A_T value "
        "sleeve, replayed point-in-time): the PM decides the joint-universe "
        "tilt from this table — 2b (NS-5 frontier) implements the chosen "
        "policy with real optimization.",
        "",
        "## Decision table",
        "",
        "| Policy | Excess yrs | Max DD | DD ratio vs SPY | Turnover/yr |",
        "|---|---|---|---|---|",
    ]
    for mode, res in res_by_mode.items():
        dd = res["drawdown"]
        lines.append(
            f"| **{mode}** | {res['excess_years']}/{len(res['yearly'])} "
            f"| {dd['strategy']:.1%} vs SPY {dd['spy']:.1%} "
            f"| {dd['ratio']:.2f} | {res['turnover']['turns_per_year']:.1f} |")
    lines += [
        "",
        "## Yearly (all three)",
        "",
        "| Year | Momentum | Value | Blend | Universe | SPY |",
        "|---|---|---|---|---|---|",
    ]
    first = next(iter(res_by_mode.values()))
    for i, y in enumerate(first["yearly"]):
        yr = y["year"]
        cells = [yr]
        for mode in TILT_MODES:
            cells.append(f"{res_by_mode[mode]['yearly'][i]['strategy']:+.1%}")
        cells.append(f"{y['universe']:+.1%}")
        cells.append(f"{y['spy']:+.1%}")
        lines.append("| " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Assumptions (PM review)",
        "",
        "1. **Tilt mapping (A1)**: growth = GDP YoY ≥ 1.5% AND CPI YoY ≤ 3.0% → "
        "70/30 momentum/value; defensive → 30/70. Step tilt — NOT NS-5's frontier sizing.",
        "2. **Value sleeve (A2)**: A_T 4-framework agreement ≥ 2, top-20, equal-weight — "
        "candidate-pool proxy. 2b sizes the joint universe on NS-5's frontier.",
        "3. **FRED as-revised (A3)** — minor lookahead, house macro accepts this.",
        "4. **No transaction costs (A4)**.",
        "",
        "## Caveats",
        "",
        "- The 0.5× DD gate applies to the FULL stack (blend + NS-5 frontier + NS-6); "
        "this harness isolates the sleeve policies.",
        "- Value sleeve replay calls A_T's validated scorer (screen_universe) — same "
        "point-in-time machinery as the momentum side.",
        "- 'momentum' here = banded top-20 with a 100% growth-sleeve weight; the "
        "G1 walk-forward's 8/11 is the same series modulo book construction.",
    ]
    FINDINGS.write_text("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=config.WF_START)
    ap.add_argument("--end", default=config.WF_END)
    ap.add_argument("--rebalance-months", type=int, default=3)
    args = ap.parse_args()

    print("loading A_T data + facts ...")
    at_db = config.AT_FUNDAMENTALS_DB
    prices = wf.load_prices(at_db)
    annual = wf.load_annual(at_db)
    membership = wf.load_membership()
    facts = wf.Facts(prices, annual, membership)
    spy = wf.load_spy(config.DATA_DIR / "spy_closes.json")
    macro = _load_macro()

    print(f"running sleeve policies {args.start} → {args.end} "
          f"(quarterly, {', '.join(TILT_MODES)}) ...")
    res_by_mode = {}
    for mode in TILT_MODES:
        print(f"  [{mode}] ...", flush=True)
        res = run_blend(args.start, args.end, facts, macro, spy,
                        rebalance_months=args.rebalance_months, tilt_mode=mode)
        res["tilt_mode"] = mode
        res["generated_at"] = datetime.now().isoformat(timespec="seconds")
        res_by_mode[mode] = res
    BLEND_RESULTS.parent.mkdir(parents=True, exist_ok=True)
    BLEND_RESULTS.write_text(json.dumps(res_by_mode, indent=2, default=str))

    print(f"\n=== NS-5 BLEND RESEARCH (2a) — {args.start} → {args.end} ===")
    print(f"{'policy':<9} {'excess yrs':>10} {'maxDD':>8} {'vs SPY':>8} {'ratio':>6} {'turn/yr':>8}")
    for mode, res in res_by_mode.items():
        dd = res["drawdown"]
        print(f"{mode:<9} {str(res['excess_years']) + '/' + str(len(res['yearly'])):>10} "
              f"{dd['strategy']:>7.1%} {dd['spy']:>7.1%} {dd['ratio']:>6.2f} "
              f"{res['turnover']['turns_per_year']:>7.1f}")
    print("--- yearly ---")
    first = next(iter(res_by_mode.values()))
    for i, y in enumerate(first["yearly"]):
        cells = [y["year"]]
        for mode in TILT_MODES:
            cells.append(f"{res_by_mode[mode]['yearly'][i]['strategy']:+.1%}")
        cells += [f"{y['universe']:+.1%}", f"{y['spy']:+.1%}"]
        print("  " + "  ".join(f"{c:>8}" for c in cells))
    write_findings(res_by_mode, {"start": args.start, "end": args.end})
    print(f"findings → {FINDINGS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
