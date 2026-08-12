"""
Option B & C selection/construction experiments.

Runs the NS-6 harness (Phase 3, equal vs momentum weighting) across a grid of
SELECTORS and WEIGHTERS to answer:

  B. Does soft-momentum sorting/weighting WITHIN the value basket help?
  C. Does momentum/cap-style weighting (concentrate toward leaders) help?

Selectors (equity picks; harness appends the fixed non-equity sleeve):
  value      — A_T screener agreement≥2, top-n by agreement (baseline)
  momentum   — top-n by trailing 6m relative return over the full universe
  value_mom  — value picks (agreement≥2, top 2n by agreement) re-ranked by
               6m momentum, take top-n (SOFT sort — momentum only reorders,
               never hard-drops)

Weighters (full selection incl. sleeve; return {ticker: weight} or None):
  equal      — equal weight across all names (baseline)
  momentum   — equity names weighted ∝ (1 + 6m mom), sleeve equal-weight,
               equity:sleeve capital split preserved (concentrate within equity)

Usage:
  python3 selection_construction_experiment.py
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

MOM_LB = 126  # ~6-month momentum lookback (trading days)


# ── Screener universe helpers ──────────────────────────────────────────────
def _screen_rows(as_of):
    """Full screener rows for an as-of date (ticker + agreement)."""
    sys.path.insert(0, os.path.join(nb._ROOT, "Project_Sequoia", "terminal"))
    import fundamental_screener as fs
    return fs.screen_universe(as_of, force=True)


def _mom_scores(closes, tickers, day, lb=MOM_LB):
    """Causal trailing-return momentum for each ticker (data ≤ day)."""
    out = {}
    for t in tickers:
        if t not in closes.columns:
            continue
        s = closes[t].dropna()
        s = s[s.index <= day]
        if len(s) > lb:
            out[t] = s.iloc[-1] / s.iloc[-lb - 1] - 1.0
    return out


# ── Selectors ──────────────────────────────────────────────────────────────
def sel_value(closes, day, top_n):
    """Baseline: screener agreement≥2, top-n by agreement."""
    rows = _screen_rows(day.strftime("%Y-%m-%d"))
    scored = sorted([r for r in rows if r["agreement"] >= 2],
                    key=lambda r: (-r["agreement"], r["ticker"]))
    return [r["ticker"] for r in scored[:top_n]]


def sel_momentum(closes, day, top_n):
    """Pure momentum: top-n by 6m return over the full screener universe."""
    rows = _screen_rows(day.strftime("%Y-%m-%d"))
    universe = [r["ticker"] for r in rows if r["ticker"] in closes.columns]
    mom = _mom_scores(closes, universe, day)
    ranked = sorted(mom, key=lambda t: -mom[t])
    return ranked[:top_n]


def sel_value_mom(closes, day, top_n):
    """Option B: value picks re-ranked by momentum (soft sort).

    Take agreement≥2 picks (top 2n by agreement), then re-rank by 6m momentum,
    keep top-n. Momentum only REORDERS the value picks — never hard-drops.
    """
    rows = _screen_rows(day.strftime("%Y-%m-%d"))
    value = sorted([r for r in rows if r["agreement"] >= 2],
                   key=lambda r: (-r["agreement"], r["ticker"]))
    pool = [r["ticker"] for r in value[: 2 * top_n]]
    mom = _mom_scores(closes, pool, day)
    # value names missing momentum (no price history) keep their agreement rank
    ranked = sorted(pool, key=lambda t: -mom.get(t, -1e9))
    return ranked[:top_n]


# ── Weighters ──────────────────────────────────────────────────────────────
def w_equal(closes, sel, day):
    """Equal weight across all names (baseline)."""
    eq = 1.0 / len(sel) if sel else 0.0
    return {t: eq for t in sel}


def w_momentum(closes, sel, day):
    """Option C: momentum-weight the equity sleeve, sleeve stays equal-weight.

    Preserves the equity:sleeve capital split from equal-weight, but within
    the equity sleeve concentrates toward leaders via w ∝ (1 + 6m mom).
    """
    equities = [t for t in sel if t not in nb.NON_EQUITY]
    sleeve = [t for t in sel if t in nb.NON_EQUITY]
    n_eq, n_ne = len(equities), len(sleeve)
    if n_eq == 0:
        return None
    eq_share = n_eq / (n_eq + n_ne)  # equity capital share (equal-weight baseline)

    mom = _mom_scores(closes, equities, day)
    raw = {t: 1.0 + max(mom.get(t, 0.0), -0.9) for t in equities}
    tot = sum(raw.values())
    if tot <= 0:
        return None
    w = {t: raw[t] / tot * eq_share for t in equities}
    ne_share = 1.0 - eq_share
    for t in sleeve:
        w[t] = ne_share / n_ne if n_ne else 0.0
    return w


# ── Run ────────────────────────────────────────────────────────────────────
def run(name, selector, weighter, closes, start, end, top_n, phase=3):
    print(f"  running {name}...", flush=True)
    r = nb.simulate(closes, start, end, top_n=top_n, phase=phase,
                    weighting="equal", selector=selector, weighter=weighter)
    return r


def main():
    start, end = "2017-01-01", "2026-08-01"
    top_n = 12
    print("# Option B & C Selection/Construction Experiments\n")
    base = nb.fetch_prices([nb.SPY, nb.VIX] + nb.CANDIDATES, 10)
    universe = nb.build_universe(base, start, end, top_n=top_n)
    closes = nb.fetch_prices([nb.SPY, nb.VIX] + universe, 10)
    print(f"window {start}..{end} | {len(closes.columns)} series | top_n={top_n} | Phase 3\n")

    configs = [
        ("value / equal (baseline)", sel_value, w_equal),
        ("value / momentum-weight",  sel_value, w_momentum),   # C on value
        ("momentum / equal",         sel_momentum, w_equal),   # A (prior result)
        ("momentum / mom-weight",    sel_momentum, w_momentum),# C on momentum
        ("value_mom / equal (B1)",   sel_value_mom, w_equal),  # B soft-sort
        ("value_mom / mom-weight (B2+C)", sel_value_mom, w_momentum),  # combined
    ]

    results = {}
    for name, sel, wgt in configs:
        results[name] = run(name, sel, wgt, closes, start, end, top_n)

    print("\n| Config | Ret% | Excess pp | Max DD% | DD ratio | Beat SPY yrs |")
    print("|--------|------|-----------|---------|----------|--------------|")
    for name, r in results.items():
        beats = sum(1 for y in r["yearly"] if r["yearly"][y]["excess"] > 0)
        ny = len(r["yearly"])
        print(f"| {name} | {r['total_port_ret']*100:.1f} | "
              f"{r['excess_total']*100:+.1f} | {r['port_max_dd']*100:.1f} | "
              f"{r['dd_ratio']:.2f} | {beats}/{ny} |")

    print(f"\nSPY: {results[configs[0][0]]['total_spy_ret']*100:.1f}% "
          f"(max DD {results[configs[0][0]]['spy_max_dd']*100:.1f}%)")

    # Yearly excess for the interesting ones
    print("\n### Yearly excess vs SPY (%)")
    cols = ["value/equal", "value_mom/equal", "value_mom/mom-wt", "momentum/equal"]
    print("| Year | " + " | ".join(c.split("/")[0] for c in cols) + " |")
    print("|------|" + "|".join("------" for _ in cols) + "|")
    years = sorted(results[configs[0][0]]["yearly"].keys())
    for y in years:
        row = []
        for c in cols:
            key = {"value/equal": "value / equal (baseline)",
                   "value_mom/equal": "value_mom / equal (B1)",
                   "value_mom/mom-wt": "value_mom / mom-weight (B2+C)",
                   "momentum/equal": "momentum / equal"}[c]
            row.append(f"{results[key]['yearly'][y]['excess']*100:+.1f}")
        print(f"| {y} | " + " | ".join(row) + " |")


if __name__ == "__main__":
    main()
