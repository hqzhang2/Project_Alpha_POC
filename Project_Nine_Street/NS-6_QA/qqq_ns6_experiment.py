"""
QQQ + NS-6 drawdown overlay experiment.

The multi-dimensional thesis: growth factor (QQQ) provides the return; NS-6
drawdown engine caps the downside. This test holds QQQ as the sole equity and
lets NS-6's exposure multiplier de-risk it into the non-equity sleeve during
drawdowns. Measures whether NS-6 can deliver growth-like return with SPY-like
(or better) drawdown.

Phases:
  1 — budget-only multiplier
  2 — multi-signal v2 multiplier + protective put drag
  3 — v2 + put drag + covered-call yield + tax proxy

Comparison: QQQ buy&hold, SPY buy&hold, and QQQ-through-NS-6 at each phase.

Usage: python3 qqq_ns6_experiment.py
"""

import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ns6_backtest as nb


def sel_qqq(closes, day, top_n):
    """QQQ as the sole equity pick (growth factor)."""
    return ["QQQ"]


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


def buyhold(ticker, closes, start, end):
    s = closes[ticker].dropna()
    s = s[(s.index >= start) & (s.index <= end)]
    return s.pct_change().dropna()


def main():
    start, end = "2017-01-01", "2026-08-01"
    top_n = 1
    print("# QQQ + NS-6 Drawdown Overlay Experiment\n")
    base = nb.fetch_prices([nb.SPY, nb.VIX, "QQQ"] + list(nb.NON_EQUITY), 10)
    universe = nb.build_universe(base, start, end, top_n=12)
    closes = nb.fetch_prices([nb.SPY, nb.VIX, "QQQ"] + universe, 10)
    print(f"window {start}..{end} | {len(closes.columns)} series\n")

    qqq_bh = buyhold("QQQ", closes, start, end)
    spy_bh = buyhold("SPY", closes, start, end)

    results = {}
    for phase in (1, 2, 3):
        print(f"  running QQQ + NS-6 phase {phase}...", flush=True)
        results[phase] = nb.simulate(closes, start, end, top_n=top_n, phase=phase,
                                     weighting="equal", selector=sel_qqq)

    # Build summary
    rows = []
    q_ann, q_vol, q_sh, q_dd = _metrics(qqq_bh)
    s_ann, s_vol, s_sh, s_dd = _metrics(spy_bh)
    rows.append(("QQQ buy&hold", q_ann, q_vol, q_sh, q_dd, None))
    rows.append(("SPY buy&hold", s_ann, s_vol, s_sh, s_dd, None))
    for phase, r in results.items():
        a, v, sh, d = _metrics(r["daily_port_ret"])
        rows.append((f"QQQ + NS-6 P{phase}", a, v, sh, d, r["dd_ratio"]))

    print("\n| Config | Ann ret% | Vol% | Sharpe | Max DD% | DD ratio |")
    print("|--------|----------|------|--------|---------|----------|")
    for name, a, v, sh, d, ratio in rows:
        dd_ratio = f"{ratio:.2f}" if ratio is not None else "—"
        print(f"| {name} | {a*100:.1f} | {v*100:.1f} | {sh:.2f} | {d*100:.1f} | {dd_ratio} |")

    # The key question: does NS-6 cut QQQ's drawdown meaningfully while keeping return?
    print("\n### The product test")
    q_dd_half = q_dd * 0.5
    print(f"  QQQ max DD: {q_dd*100:.1f}%  → half = {q_dd_half*100:.1f}%")
    print(f"  SPY max DD: {s_dd*100:.1f}%")
    for phase, r in results.items():
        a, v, sh, d = _metrics(r["daily_port_ret"])
        ret_kept = a / q_ann * 100
        dd_kept = abs(d) / abs(q_dd) * 100
        print(f"  P{phase}: DD {d*100:.1f}% ({dd_kept:.0f}% of QQQ), "
              f"ann {a*100:.1f}% ({ret_kept:.0f}% of QQQ), Sharpe {sh:.2f}")


if __name__ == "__main__":
    main()
