#!/usr/bin/env python3
"""
NS-6 Walk-Forward Portfolio Backtest Harness (Phase 1)
======================================================
End-to-end validation of the full pipeline vs SPY:

  A_T screener (agreement ≥ 2) → + non-equity ETFs → NS-6 exposure
  multiplier (Phase 1: budget-only) → quarterly rebalance funding.

Measures the Phase 1 ACCEPTANCE GATE:
  - positive excess return vs SPY in ≥7/10 years
  - max drawdown ≤ SPY max drawdown × 0.5 in ≥8/10 years
  - average <30 trades/quarter

Deterministic. No LLM in the compute path. Reuses the screener as-of
semantics (point-in-time fundamentals) and NS-6 pure modules.

Usage:
  python3 ns6_backtest.py                     # default window
  python3 ns6_backtest.py --years 10 --out path.json
  python3 ns6_backtest.py --pickle /tmp/prices.pkl   # reuse cached prices

Price data is fetched once via yfinance and cached to a pickle (the
ratio-walkforward pattern) so re-runs are offline and reproducible.
"""

import argparse
import json
import os
import pickle
import sys
import warnings
from datetime import datetime

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# Repo-root sys.path bootstrap (mirrors qa_server.py runtime).
# __file__ = .../Project_Alpha_POC/Project_Nine_Street/NS-6_QA/ns6_backtest.py
# two ".." up = Project_Alpha_POC (three would overshoot to /Users/chuck).
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
# NS-6_QA must win name resolution (config.py) over Project_Sequoia/terminal's.
# insert(0) puts the LAST insert at the FRONT. NS-6_QA is already sys.path[0]
# (script dir) but Project_Sequoia would shadow it — force NS-6 to front LAST.
for p in (os.path.join(_ROOT, "Project_Sequoia", "terminal"), _ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)
_NS6 = os.path.dirname(os.path.abspath(__file__))
if _NS6 in sys.path:
    sys.path.remove(_NS6)
sys.path.insert(0, _NS6)

import budget as budget_mod
import config as config_mod
import enforcement as enforcement_mod
import rebalance as rebalance_mod

# ── Fixed candidate universe (liquid SP500 names spanning sectors) ──────
# Plus non-equity ETFs (the drawdown/income sleeve).
CANDIDATES = [
    # tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "ORCL", "ADBE", "CRM",
    # financials
    "JPM", "BAC", "WFC", "GS", "MS", "C", "BLK",
    # industrials / defense
    "LMT", "RTX", "CAT", "HON", "GE", "BA", "UNP",
    # energy
    "XOM", "CVX", "COP", "SLB",
    # healthcare
    "UNH", "JNJ", "PFE", "MRK", "ABBV", "LLY", "TMO", "ISRG",
    # consumer
    "WMT", "COST", "PG", "KO", "PEP", "MCD", "NKE", "SBUX",
    # non-equity ETFs (the sleeve)
    "TLT", "GLD", "IEF", "BIL", "DBC",
]
NON_EQUITY = {"TLT", "GLD", "IEF", "BIL", "DBC"}
SPY = "SPY"

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ns6_prices.pkl")
REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", "..", "research_2026-08_ns6_backtest.md")


# ── Price data (pickle cache) ──────────────────────────────────────────────
def fetch_prices(tickers, years, force=False):
    """Daily closes for tickers over the window. Cached to pickle.

    Fetches only the missing tickers and merges into the existing cache.
    """
    cached = None
    if os.path.exists(CACHE) and not force:
        with open(CACHE, "rb") as f:
            cached = pickle.load(f)
    missing = [t for t in tickers
               if cached is None or t not in cached.columns]
    if not missing and cached is not None and not cached.empty:
        return cached

    import yfinance as yf
    new = {}
    for t in missing:
        try:
            df = yf.Ticker(t).history(period=f"{years}y", interval="1d",
                                      auto_adjust=True)
            new[t] = df["Close"]
        except Exception as e:
            print(f"  fetch {t} failed: {e}", flush=True)

    if cached is not None and not cached.empty:
        # normalize index (tz-naive vs tz-aware mismatch on join)
        cached.index = pd.to_datetime(cached.index).tz_localize(None)
        new_df = pd.DataFrame(new)
        new_df.index = pd.to_datetime(new_df.index).tz_localize(None)
        merged = cached.join(new_df, how="outer")
    else:
        merged = pd.DataFrame(new)
        merged.index = pd.to_datetime(merged.index).tz_localize(None)
    merged.index = pd.to_datetime(merged.index).tz_localize(None)
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "wb") as f:
        pickle.dump(merged, f)
    return merged


def build_universe(closes, start, end, top_n=12, min_agreement=2):
    """Union of SPY + non-equity ETFs + all screener picks across rebalance dates.

    The screener selects value/quality names (KDP, TROW, WSM, BLDR...) that are
    NOT mega-caps — a fixed mega-cap candidate list would overlap only 0-3/12
    and falsely empty the equity sleeve. Honest universe = whatever the screener
    actually picks. Returns ALL picks (does NOT filter to closes.columns —
    missing ones are fetched afterward).
    """
    dates = closes[SPY].dropna().index
    picks = set(NON_EQUITY)
    for d in pd.date_range(start=start, end=end, freq="QE"):
        prior = dates[dates <= d]
        if not len(prior):
            continue
        picks |= set(select_stocks(prior[-1].strftime("%Y-%m-%d"),
                                   top_n=top_n, min_agreement=min_agreement))
    return sorted(picks)


# ── Screener selection (as-of, point-in-time) ──────────────────────────────
def select_stocks(as_of, top_n=12, min_agreement=2):
    """Return tickers with agreement ≥ min_agreement, capped at top_n."""
    sys.path.insert(0, os.path.join(_ROOT, "Project_Sequoia", "terminal"))
    import fundamental_screener as fs
    rows = fs.screen_universe(as_of, force=True)
    scored = sorted([r for r in rows if r["agreement"] >= min_agreement],
                    key=lambda r: (-r["agreement"], r["ticker"]))
    return [r["ticker"] for r in scored[:top_n]]


# ── Portfolio simulation ────────────────────────────────────────────────────
def simulate(closes, start, end, top_n=12, cost_bps=10.0):
    """Run quarterly rebalance with NS-6 budget-only multiplier.

    Returns dict: {years: {year: {port_ret, spy_ret, max_dd, spy_max_dd}},
                   trades_per_quarter, daily_curve (for SPY drawdown), ...}
    """
    theta = config_mod.load_theta()
    # trading dates from SPY
    spy = closes[SPY].dropna()
    dates = spy.index
    valid = closes.reindex(dates).ffill()

    # Build quarterly rebalance dates → nearest prior trading day.
    reb_days = []
    for d in pd.date_range(start=start, end=end, freq="QE"):
        prior = dates[dates <= d]
        if len(prior):
            reb_days.append(prior[-1])
    reb_days = list(dict.fromkeys(reb_days))  # dedupe, keep order

    # equal target weights for a selection
    def target_weights(sel):
        eq = 1.0 / len(sel) if sel else 0.0
        return {t: eq for t in sel}

    portfolio = {}  # ticker -> weight (fraction of NAV)
    cash_weight = 0.0  # remainder held in BIL
    last_weights = {}
    daily_port_ret = pd.Series(0.0, index=dates)
    daily_spy_ret = spy.pct_change().fillna(0.0)

    quarter_trades = []

    for i, day in enumerate(reb_days):
        as_of = day.strftime("%Y-%m-%d")

        # 1. Select stocks (screener) + fixed non-equity sleeve
        sel = [t for t in select_stocks(as_of, top_n=top_n) if t in valid.columns]
        sel = sel + [t for t in NON_EQUITY if t in valid.columns]
        sel = list(dict.fromkeys(sel))  # dedupe, keep order

        # 2. Equal-weight target across the selection.
        tgt = target_weights(sel)

        # 3. NS-6 exposure multiplier from trailing SPY drawdown.
        spy_history = valid[SPY].iloc[: dates.get_loc(day) + 1]
        spy_dd = budget_mod.compute_spy_drawdown(spy_history.tolist())
        budget_pct = budget_mod.compute_budget(spy_dd, theta)
        cur_dd = budget_mod.compute_drawdown(spy_history.tolist())
        remaining = budget_mod.budget_remaining(cur_dd, budget_pct, theta)
        multiplier = enforcement_mod.compute_exposure_multiplier(remaining, theta)

        # Apply multiplier to equity sleeve only (non-equity unchanged).
        eff_tgt = {}
        for t, w in tgt.items():
            eff_tgt[t] = w * (multiplier if t not in NON_EQUITY else 1.0)
        # normalize so total = 1
        tot = sum(eff_tgt.values())
        if tot > 0:
            eff_tgt = {t: w / tot for t, w in eff_tgt.items()}

        # 4. Funding via rebalance module (moves last_weights → eff_tgt)
        prices_now = {t: float(valid[t].iloc[dates.get_loc(day)]) for t in eff_tgt}
        paths = rebalance_mod.generate_funding_paths(
            last_weights, eff_tgt, 1_000_000, prices=prices_now, theta=theta)
        chosen = paths[0] if paths else None
        n_trades = len(chosen["trades"]) if chosen else 0
        quarter_trades.append(n_trades)

        # Simulate returns to next rebalance with the NEW weights.
        portfolio = eff_tgt
        # trade cost: cost_bps per side × total weight changed
        changed = sum(abs(eff_tgt.get(t, 0) - last_weights.get(t, 0))
                      for t in set(eff_tgt) | set(last_weights))
        cost = changed * (cost_bps / 1e4)

        start_i = dates.get_loc(day)
        end_i = (dates.get_loc(reb_days[i + 1]) if i + 1 < len(reb_days)
                 else len(dates) - 1)
        seg_ret = valid.reindex(dates[start_i:end_i + 1]).pct_change().fillna(0.0)
        for j in range(start_i, end_i + 1):
            wts = {t: w for t, w in portfolio.items() if t in seg_ret.columns}
            r = sum(wts.get(t, 0) * seg_ret[t].iloc[j - start_i] for t in wts)
            if j == start_i:
                r -= cost  # pay trade cost on rebalance day
            daily_port_ret.iloc[j] = r
        last_weights = eff_tgt

    # Daily portfolio value & drawdown
    port_cum = (1 + daily_port_ret).cumprod()
    spy_cum = (1 + daily_spy_ret).cumprod()

    def max_dd(cum):
        roll = cum.cummax()
        return float(((cum - roll) / roll).min())

    port_max_dd = max_dd(port_cum)
    spy_max_dd = max_dd(spy_cum)

    # Yearly breakdown
    yearly = {}
    y_ret = daily_port_ret.groupby(daily_port_ret.index.year).apply(
        lambda s: float((1 + s).prod() - 1))
    y_spy = daily_spy_ret.groupby(daily_spy_ret.index.year).apply(
        lambda s: float((1 + s).prod() - 1))
    for yr in y_ret.index:
        yr_mask = daily_port_ret.index.year == yr
        yearly[int(yr)] = {
            "port_ret": y_ret[yr],
            "spy_ret": y_spy.get(yr, 0.0),
            "excess": y_ret[yr] - y_spy.get(yr, 0.0),
            "port_max_dd": max_dd(port_cum[yr_mask]),
            "spy_max_dd": max_dd(spy_cum[yr_mask]),
        }

    total_port = float(port_cum.iloc[-1] - 1)
    total_spy = float(spy_cum.iloc[-1] - 1)

    return {
        "total_port_ret": total_port,
        "total_spy_ret": total_spy,
        "excess_total": total_port - total_spy,
        "port_max_dd": port_max_dd,
        "spy_max_dd": spy_max_dd,
        "dd_ratio": port_max_dd / spy_max_dd if spy_max_dd else None,
        "trades_per_quarter": quarter_trades,
        "avg_trades_per_quarter": float(np.mean(quarter_trades)) if quarter_trades else 0.0,
        "yearly": yearly,
    }


# ── Acceptance gate ─────────────────────────────────────────────────────────
def evaluate(results):
    """Apply the Phase 1 acceptance gate. Returns (pass_bool, details)."""
    yearly = results["yearly"]
    years = sorted(yearly.keys())
    if not years:
        return False, {"error": "no years"}
    excess_ok = sum(1 for y in years if yearly[y]["excess"] > 0)
    dd_ok = sum(1 for y in years
                if yearly[y]["spy_max_dd"] < 0
                and abs(yearly[y]["port_max_dd"]) <= abs(yearly[y]["spy_max_dd"]) * 0.5)
    trades_ok = results["avg_trades_per_quarter"] < 30
    n = len(years)
    passed = (excess_ok >= int(0.7 * n) and dd_ok >= int(0.8 * n) and trades_ok)
    return passed, {
        "n_years": n,
        "excess_positive_years": excess_ok,
        "dd_halved_years": dd_ok,
        "avg_trades_per_quarter": results["avg_trades_per_quarter"],
        "trades_gate_ok": trades_ok,
        "passed": passed,
    }


# ── Report ──────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="NS-6 walk-forward backtest")
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--start", default="2017-01-01")
    ap.add_argument("--end", default="2026-08-01")
    ap.add_argument("--top-n", type=int, default=12)
    ap.add_argument("--cost-bps", type=float, default=10.0)
    ap.add_argument("--force-fetch", action="store_true")
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "ns6_backtest_results.json"))
    args = ap.parse_args()

    print(f"# NS-6 Walk-Forward Backtest — {datetime.now():%Y-%m-%d %H:%M}")
    print(f"window={args.start}..{args.end} | top_n={args.top_n} | "
          f"cost={args.cost_bps}bps | years={args.years}\n")

    # Discover the screener picks first (needs the fundamentals store, fast),
    # then fetch prices for the HONEST universe (whatever the screener picks).
    print("  discovering screener universe...", flush=True)
    base = fetch_prices([SPY] + CANDIDATES, args.years, force=args.force_fetch)
    universe = build_universe(base, args.start, args.end, top_n=args.top_n)
    # fetch_prices merges missing picks into the cache internally
    closes = fetch_prices([SPY] + universe, args.years)
    print(f"  universe: {len(universe)} names, {len(closes.columns)} series "
          f"({len(closes)} days)")

    print("  simulating...", flush=True)
    results = simulate(closes, args.start, args.end, top_n=args.top_n,
                       cost_bps=args.cost_bps)

    passed, gate = evaluate(results)
    print(f"\n## Acceptance Gate: {'PASS' if passed else 'FAIL'}")
    for k, v in gate.items():
        print(f"  {k}: {v}")

    print("\n## Yearly")
    print("| Year | Port% | SPY% | Excess% | Port MaxDD% | SPY MaxDD% |")
    print("|------|-------|------|---------|-------------|------------|")
    for y in sorted(results["yearly"]):
        v = results["yearly"][y]
        print(f"| {y} | {v['port_ret']*100:.1f} | {v['spy_ret']*100:.1f} | "
              f"{v['excess']*100:+.1f} | {v['port_max_dd']*100:.1f} | "
              f"{v['spy_max_dd']*100:.1f} |")

    print(f"\n## Totals")
    print(f"  portfolio: {results['total_port_ret']*100:.1f}% | "
          f"SPY: {results['total_spy_ret']*100:.1f}% | "
          f"excess: {results['excess_total']*100:+.1f}pp")
    print(f"  portfolio max DD: {results['port_max_dd']*100:.1f}% | "
          f"SPY max DD: {results['spy_max_dd']*100:.1f}% | "
          f"ratio: {results['dd_ratio']:.2f}")
    print(f"  trades/quarter: {results['avg_trades_per_quarter']:.1f}")

    out = {"generated": datetime.now().isoformat(), "config": vars(args),
           "results": results, "gate": gate}
    with open(args.out, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nJSON: {args.out}")

    # Research report (gitignored)
    with open(REPORT, "w") as f:
        f.write(f"# NS-6 Walk-Forward Backtest — {datetime.now():%Y-%m-%d}\n\n")
        f.write(f"Gate: **{'PASS' if passed else 'FAIL'}**\n\n")
        for k, v in gate.items():
            f.write(f"- {k}: {v}\n")
        f.write(f"\n```json\n{json.dumps(results, indent=2, default=str)}\n```\n")
    print(f"Report: {REPORT}")


if __name__ == "__main__":
    main()
