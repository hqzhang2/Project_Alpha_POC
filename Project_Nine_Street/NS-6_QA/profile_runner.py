"""
profile_runner.py — run the three switchable PM target points.

Resolves a profile (growth / balanced / capital_preservation) into a
concrete backtest config (selection + weighting + theta overrides), runs it
through the NS-6 harness with fast de-risking, and reports the return/drawdown
point each profile lands on. This is the PM cockpit: all three points computed,
PM switches among them by regime/conviction.

Usage:
  python3 profile_runner.py               # all three profiles
  python3 profile_runner.py --profile growth
"""

import argparse
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import ns6_backtest as nb

MAG7 = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA"]


def _metrics(r):
    d = r.dropna()
    ann = (1 + d).prod() ** (252 / len(d)) - 1
    vol = d.std() * np.sqrt(252)
    sharpe = ann / vol if vol > 0 else 0.0
    cum = (1 + d).cumprod()
    mdd = ((cum - cum.cummax()) / cum.cummax()).min()
    return ann, vol, sharpe, mdd


# ── Selection resolvers ───────────────────────────────────────────────────
def _sel_growth(closes, day, top_n):
    """MAG7 growth basket (the return engine)."""
    return [t for t in MAG7 if t in closes.columns]


def _sel_value(closes, day, top_n):
    """Value/quality screener (defensive tilt)."""
    return nb.select_stocks(day.strftime("%Y-%m-%d"), top_n=top_n)


# ── Weighting resolvers ───────────────────────────────────────────────────
def _w_equal(closes, sel, day):
    eq = 1.0 / len(sel) if sel else 0.0
    return {t: eq for t in sel}


def _w_gmv(closes, sel, day):
    return nb._ns5_target_weights(closes, sel, day, method="gmv")


def _w_growth_sleeve_60_40(closes, sel, day):
    """60% equity (growth) / 40% non-equity sleeve, equal within each leg."""
    eq = [t for t in sel if t not in nb.NON_EQUITY]
    ne = [t for t in sel if t in nb.NON_EQUITY]
    w = {}
    if eq:
        for t in eq:
            w[t] = 0.60 / len(eq)
    if ne:
        for t in ne:
            w[t] = 0.40 / len(ne)
    return w


def _w_growth_90_10(closes, sel, day):
    """90% equity (growth) / 10% sleeve — the return-max point."""
    eq = [t for t in sel if t not in nb.NON_EQUITY]
    ne = [t for t in sel if t in nb.NON_EQUITY]
    w = {}
    if eq:
        for t in eq:
            w[t] = 0.90 / len(eq)
    if ne:
        for t in ne:
            w[t] = 0.10 / len(ne)
    return w


SELECTORS = {"growth_basket": _sel_growth, "value_screener": _sel_value}
WEIGHTERS = {"equal": _w_equal, "gmv": _w_gmv,
             "growth_sleeve_60_40": _w_growth_sleeve_60_40,
             "growth_90_10": _w_growth_90_10}


def run_profile(name, closes, start, end, fast_derisk=True, phase=3):
    """Run one profile. Returns dict with metrics + the raw simulate result."""
    theta, sel_name, wgt_name = config.load_profile(name)
    selector = SELECTORS[sel_name]
    weighter = WEIGHTERS[wgt_name]
    top_n = len(MAG7) if sel_name == "growth_basket" else 12
    r = nb.simulate(closes, start, end, top_n=top_n, phase=phase,
                    weighting="equal", selector=selector, weighter=weighter,
                    fast_derisk=fast_derisk, theta=theta)
    ann, vol, sharpe, mdd = _metrics(r["daily_port_ret"])
    return {"name": name, "label": config.PROFILES[name]["label"],
            "ann": ann, "vol": vol, "sharpe": sharpe, "mdd": mdd,
            "result": r}


def main():
    ap = argparse.ArgumentParser(description="Run NS-6 PM target-point profiles")
    ap.add_argument("--profile", choices=sorted(config.PROFILES), default=None)
    ap.add_argument("--start", default="2017-01-01")
    ap.add_argument("--end", default="2026-08-01")
    ap.add_argument("--phase", type=int, default=3, choices=[1, 2, 3])
    args = ap.parse_args()

    print("# NS-6 PM Target-Point Profiles (switchable)\n")
    closes = nb.fetch_prices([nb.SPY, nb.VIX, "QQQ", "BIL"] + MAG7 + nb.CANDIDATES, 10)
    print(f"window {args.start}..{args.end} | phase {args.phase} | fast-de-risk on\n")

    names = [args.profile] if args.profile else list(config.PROFILES)
    results = []
    for name in names:
        print(f"  running {name}...", flush=True)
        results.append(run_profile(name, closes, args.start, args.end,
                                   fast_derisk=True, phase=args.phase))

    # benchmarks
    def bh(t):
        s = closes[t].dropna()
        s = s[(s.index >= args.start) & (s.index <= args.end)]
        return s.pct_change().dropna()
    spy_ann, spy_vol, spy_sh, spy_dd = _metrics(bh("SPY"))
    qqq_ann, _, qqq_sh, qqq_dd = _metrics(bh("QQQ"))

    print("\n| Target point | Ann ret% | Sharpe | Max DD% | vs SPY (Sharpe) | vs SPY (DD) |")
    print("|--------------|----------|--------|---------|-----------------|-------------|")
    print(f"| SPY buy&hold | {spy_ann*100:.1f} | {spy_sh:.2f} | {spy_dd*100:.1f} | — | — |")
    for r in results:
        dd_note = "better" if abs(r["mdd"]) < abs(spy_dd) else "worse"
        print(f"| {r['label']} | {r['ann']*100:.1f} | {r['sharpe']:.2f} | "
              f"{r['mdd']*100:.1f} | {r['sharpe']-spy_sh:+.2f} | {dd_note} |")

    print("\n### Switch guidance")
    print("  growth               — when growth regime (R1/R2) & high conviction")
    print("  balanced             — default / mixed regime")
    print("  capital_preservation — when defensive regime (R3/R4) or drawdown budget tight")


if __name__ == "__main__":
    main()
