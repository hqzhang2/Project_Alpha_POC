"""
Quick experiment: does stock selection explain the return gap?

Runs the NS-6 backtest with TWO selection methods over the same window:
  A) VALUE:  A_T fundamental screener (agreement >= 2), top-12
  B) MOMENTUM: top-12 by trailing 6-month return from the candidate pool
Both use the SAME NS-6 engine (Phase 3, equal-weight) so the ONLY variable
is which stocks get selected. Isolates selection vs engine as the cause.

Usage: python3 selection_gap_experiment.py
Output: side-by-side comparison + whether the gap closes.
"""

import os
import sys
import warnings
from datetime import datetime

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ns6_backtest as nb

THETA = None


def _momentum_select(closes, as_of, top_n=12):
    """Top-n tickers by trailing ~126-day return, from the full screener universe."""
    # Get the full universe the value screener considers.
    sys.path.insert(0, os.path.join(nb._ROOT, "Project_Sequoia", "terminal"))
    import fundamental_screener as fs
    try:
        rows = fs.screen_universe(as_of.strftime("%Y-%m-%d"), force=True)
        universe = [r["ticker"] for r in rows if r["ticker"] in closes.columns]
    except Exception:
        universe = [t for t in nb.CANDIDATES if t != nb.SPY and t in closes.columns]
    universe = [t for t in universe if t not in nb.NON_EQUITY and t != nb.SPY]
    if not universe:
        return []
    sub = closes[universe][closes.index <= as_of]
    if len(sub) < 130:
        return []
    mom = sub.iloc[-126:].iloc[-1] / sub.iloc[-126:].iloc[0] - 1.0
    mom = mom.dropna().sort_values(ascending=False)
    # drop non-equity ETFs from momentum selection (they're the sleeve, added separately)
    return [t for t in mom.index if t not in nb.NON_EQUITY][:top_n]


def _run_with_selector(closes, start, end, phase, weighting, selector):
    """Run simulate with a custom select_stocks replacement."""
    orig = nb.select_stocks
    nb.select_stocks = lambda as_of, top_n=12, min_agreement=2: selector(closes, pd.Timestamp(as_of), top_n)
    try:
        return nb.simulate(closes, start, end, top_n=12, phase=phase, weighting=weighting)
    finally:
        nb.select_stocks = orig


def main():
    start, end = "2017-01-01", "2026-08-01"
    print("# Selection Gap Experiment — value vs momentum selection\n")
    # Build the full universe (candidates + all screener picks) so both
    # selectors see the same price pool.
    base = nb.fetch_prices([nb.SPY, nb.VIX] + nb.CANDIDATES, 10)
    universe = nb.build_universe(base, start, end, top_n=12)
    closes = nb.fetch_prices([nb.SPY, nb.VIX] + universe, 10)
    print(f"window {start}..{end} | {len(closes.columns)} series | Phase 3 equal-weight\n")

    print("  running value-screener selection...", flush=True)
    r_value = _run_with_selector(closes, start, end, 3, "equal", _value_selector)
    print("  running momentum selection...", flush=True)
    r_mom = _run_with_selector(closes, start, end, 3, "equal", _momentum_select)

    print("| Metric | Value screen | Momentum | Delta |")
    print("|--------|--------------|----------|-------|")
    rows = [
        ("total port ret%", r_value['total_port_ret']*100, r_mom['total_port_ret']*100),
        ("total SPY ret%", r_value['total_spy_ret']*100, r_mom['total_spy_ret']*100),
        ("excess pp", r_value['excess_total']*100, r_mom['excess_total']*100),
        ("port max DD%", r_value['port_max_dd']*100, r_mom['port_max_dd']*100),
    ]
    for name, v1, v2 in rows:
        print(f"| {name} | {v1:.1f} | {v2:.1f} | {v2-v1:+.1f} |")

    print("\n### Yearly excess (port% - spy%)")
    print("| Year | Value | Momentum |")
    print("|------|-------|----------|")
    for y in sorted(r_value["yearly"]):
        ev = r_value["yearly"][y]["excess"]*100
        em = r_mom["yearly"][y]["excess"]*100
        print(f"| {y} | {ev:+.1f} | {em:+.1f} |")

    print(f"\nValue: {r_value['total_port_ret']*100:.1f}% vs SPY {r_value['total_spy_ret']*100:.1f}% "
          f"(excess {r_value['excess_total']*100:+.1f}pp)")
    print(f"Momentum: {r_mom['total_port_ret']*100:.1f}% vs SPY {r_mom['total_spy_ret']*100:.1f}% "
          f"(excess {r_mom['excess_total']*100:+.1f}pp)")


def _value_selector(closes, as_of, top_n=12):
    """Wrap the A_T fundamental screener (same as nb.select_stocks default)."""
    sys.path.insert(0, os.path.join(nb._ROOT, "Project_Sequoia", "terminal"))
    import fundamental_screener as fs
    rows = fs.screen_universe(as_of.strftime("%Y-%m-%d"), force=True)
    scored = sorted([r for r in rows if r["agreement"] >= 2],
                    key=lambda r: (-r["agreement"], r["ticker"]))
    return [r["ticker"] for r in scored[:top_n]]


if __name__ == "__main__":
    main()
