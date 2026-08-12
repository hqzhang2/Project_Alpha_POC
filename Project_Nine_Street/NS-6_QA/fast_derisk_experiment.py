"""
Fast de-risking experiment (Option 1): NS-1 VIX-smile exposure on QQQ.

Tests whether a FAST de-risking mechanism (daily VIX-smile exposure, as NS-1
proved works) can hold the growth factor (QQQ) while capping drawdown —
versus NS-6's SLOW quarterly budget-multiplier which the prior experiment
showed is net-negative on growth.

VIX smile (from NS-1 v3, capital_preservation profile) — exposure cap by VIX:
  <12: 0.95, 12-15: 1.00, 15-20: 0.90, 20-25: 0.80, 25-30: 0.65,
  30-35: 0.50, 35-40: 0.35, 40-50: 0.55, 50-60: 0.70, 60+: 0.85

Crisis hysteresis: VIX >= 28 -> exposure 0 (safe havens); exit at <= 23.

NO LOOKAHEAD: exposure on day t uses VIX close at t-1 (decide on prior close).

Remainder of capital goes to BIL (yield-bearing cash).

Usage: python3 fast_derisk_experiment.py
"""

import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ns6_backtest as nb

# NS-1 v3 VIX smile (capital_preservation profile)
VIX_SMILE = [(12, 0.95), (15, 1.00), (20, 0.90), (25, 0.80), (30, 0.65),
             (35, 0.50), (40, 0.35), (50, 0.55), (60, 0.70), (100, 0.85)]
CRISIS_IN = 28.0
CRISIS_OUT = 23.0


def vix_cap(vix):
    """Exposure cap for a VIX level via the smile curve."""
    if vix is None or np.isnan(vix):
        return 0.65
    for level, cap in VIX_SMILE:
        if vix < level:
            return cap
    return 0.85


def _metrics(r):
    r = r.dropna()
    if len(r) < 60:
        return 0.0, 0.0, 0.0, 0.0
    ann = (1 + r).prod() ** (252 / len(r)) - 1
    vol = r.std() * np.sqrt(252)
    sharpe = ann / vol if vol > 0 else 0.0
    cum = (1 + r).cumprod()
    mdd = ((cum - cum.cummax()) / cum.cummax()).min()
    return ann, vol, sharpe, mdd


def run_fast_derisk(closes, start, end, crisis=True, lag=1, cap_floor=None):
    """Daily VIX-smile exposure on QQQ, remainder in BIL. No lookahead.

    exposure[t] = vix_cap(VIX[t-lag]); crisis mode forces 0 when VIX >= 28.
    cap_floor: if set, never let exposure drop below this (e.g. 0.2 = keep 20%).
    """
    qqq = closes["QQQ"].dropna()
    bil = closes["BIL"].dropna() if "BIL" in closes.columns else closes["TLT"].dropna()
    vix = closes["^VIX"].dropna()
    idx = closes["SPY"].dropna().index
    idx = idx[(idx >= start) & (idx <= end)]

    qqq_r = qqq.reindex(idx).pct_change().fillna(0.0)
    bil_r = bil.reindex(idx).pct_change().fillna(0.0)
    vix_lag = vix.reindex(idx).shift(lag)  # decide on prior close

    crisis_mode = False
    exposure = []
    for t in idx:
        v = vix_lag.loc[t]
        if crisis:
            if v >= CRISIS_IN:
                crisis_mode = True
            elif v <= CRISIS_OUT:
                crisis_mode = False
        if crisis_mode:
            e = 0.0
        else:
            e = vix_cap(v)
        if cap_floor is not None:
            e = max(e, cap_floor)
        exposure.append(e)
    exposure = pd.Series(exposure, index=idx)

    ret = exposure * qqq_r + (1.0 - exposure) * bil_r
    return ret, exposure


def main():
    start, end = "2017-01-01", "2026-08-01"
    print("# Fast De-Risking Experiment (Option 1): NS-1 VIX-smile on QQQ\n")
    base = nb.fetch_prices([nb.SPY, nb.VIX, "QQQ", "BIL"], 10)
    closes = nb.fetch_prices([nb.SPY, nb.VIX, "QQQ", "BIL", "TLT"], 10)
    print(f"window {start}..{end}\n")

    # baselines
    def bh(t):
        s = closes[t].dropna()
        s = s[(s.index >= start) & (s.index <= end)]
        return s.pct_change().dropna()

    qqq_r, qqq_exp = run_fast_derisk(closes, start, end, crisis=True, cap_floor=None)
    # variants
    v_nofloor, _ = run_fast_derisk(closes, start, end, crisis=True, cap_floor=None)
    v_floor20, _ = run_fast_derisk(closes, start, end, crisis=True, cap_floor=0.20)
    v_nocrisis, _ = run_fast_derisk(closes, start, end, crisis=False, cap_floor=None)

    rows = [("QQQ buy&hold", *_metrics(bh("QQQ")), None),
            ("SPY buy&hold", *_metrics(bh("SPY")), None),
            ("VIX-smile + crisis", *_metrics(v_nofloor), None),
            ("VIX-smile + crisis + 20% floor", *_metrics(v_floor20), None),
            ("VIX-smile only (no crisis)", *_metrics(v_nocrisis), None)]

    print("| Config | Ann ret% | Vol% | Sharpe | Max DD% |")
    print("|--------|----------|------|--------|---------|")
    for name, a, v, sh, d, _ in rows:
        print(f"| {name} | {a*100:.1f} | {v*100:.1f} | {sh:.2f} | {d*100:.1f} |")

    # avg exposure
    print("\n### Exposure stats")
    print(f"  VIX-smile + crisis: avg exposure {qqq_exp.mean()*100:.1f}%, "
          f"min {qqq_exp.min()*100:.1f}%, days at 0 = {(qqq_exp==0).sum()}")
    _, e2 = run_fast_derisk(closes, start, end, crisis=True, cap_floor=0.20)
    print(f"  +20% floor: avg {e2.mean()*100:.1f}%, days at 0 = {(e2==0).sum()}")


if __name__ == "__main__":
    main()
