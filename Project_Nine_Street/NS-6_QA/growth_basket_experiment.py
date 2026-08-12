"""
Growth-basket diversification experiment (v3 scope).

Tests the final lever for the "half the drawdown" mandate: does DIVERSIFYING
the growth factor (a basket of mega-cap growth names) + fast de-risking halve
drawdown vs single QQQ?

The research skill warns of "fake diversification": if all names load on the
same factor (momentum/tech/duration), the basket is just as fragile as one
name. This experiment measures whether growth names are diversified enough
to help, or whether they crash together.

Baskets:
  QQQ        — single growth asset (baseline, -35% DD)
  MAG7       — AAPL MSFT NVDA GOOGL AMZN META TSLA (equal weight)
  MAG5_exTSLA— AAPL MSFT NVDA GOOGL AMZN (drop the highest-beta name)
  QQQ+leaders— QQQ + MAG5

Fast de-risking applied (VIX smile + floored crisis, 30% floor).

Usage: python3 growth_basket_experiment.py
"""

import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ns6_backtest as nb
import fast_derisk_experiment as fe

MAG7 = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA"]
MAG5 = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"]


def _metrics(r):
    r = r.dropna()
    ann = (1 + r).prod() ** (252 / len(r)) - 1
    vol = r.std() * np.sqrt(252)
    sharpe = ann / vol if vol > 0 else 0.0
    cum = (1 + r).cumprod()
    mdd = ((cum - cum.cummax()) / cum.cummax()).min()
    return ann, vol, sharpe, mdd


def run_basket(closes, tickers, start, end, crisis=True, floor=0.30):
    """Fast de-risking on a basket of growth names (equal weight) vs BIL."""
    idx = closes["SPY"].dropna().index
    idx = idx[(idx >= start) & (idx <= end)]
    basket_ret = pd.Series(0.0, index=idx)
    n = 0
    for t in tickers:
        if t in closes.columns:
            s = closes[t].reindex(idx).pct_change().fillna(0.0)
            basket_ret += s
            n += 1
    if n == 0:
        return None
    basket_ret /= n

    bil = closes["BIL"].reindex(idx).pct_change().fillna(0.0)
    vix = closes["^VIX"].dropna().reindex(idx).shift(1)

    crisis_mode = False
    exposure = []
    for t in idx:
        v = vix.loc[t]
        if v >= 28.0:
            crisis_mode = True
        elif v <= 23.0:
            crisis_mode = False
        if crisis_mode:
            e = floor
        else:
            e = fe.vix_cap(v)
        exposure.append(e)
    exposure = pd.Series(exposure, index=idx)

    ret = exposure * basket_ret + (1.0 - exposure) * bil
    return ret


def main():
    start, end = "2017-01-01", "2026-08-01"
    print("# Growth-Basket Diversification Experiment (v3)\n")
    closes = nb.fetch_prices([nb.SPY, nb.VIX, "QQQ", "BIL"] + MAG7, 10)
    print(f"window {start}..{end}\n")

    # pairwise correlation of growth names (the fake-diversification test)
    idx = closes["SPY"].dropna().index
    idx = idx[(idx >= start) & (idx <= end)]
    rets = pd.DataFrame({t: closes[t].reindex(idx).pct_change()
                         for t in ["QQQ"] + MAG7 if t in closes.columns}).dropna()
    print("### Pairwise return correlation (growth names, 2017-2026)")
    corr = rets.corr()
    print(corr.round(2).to_string())
    print(f"\n  avg off-diagonal corr: "
          f"{corr.values[np.triu_indices_from(corr.values, k=1)].mean():.2f}\n")

    print("### Fast de-risking (30% floor) results")
    print("| Basket | Ann ret% | Sharpe | Max DD% |")
    print("|--------|----------|--------|---------|")
    qqq_bh = closes["QQQ"][closes["QQQ"].index >= start].dropna()
    qqq_bh = qqq_bh[(qqq_bh.index <= end)].pct_change().dropna()
    a, v, sh, d = _metrics(qqq_bh)
    print(f"| QQQ buy&hold | {a*100:.1f} | {sh:.2f} | {d*100:.1f} |")

    spy_bh = closes["SPY"][closes["SPY"].index >= start].dropna()
    spy_bh = spy_bh[(spy_bh.index <= end)].pct_change().dropna()
    a, v, sh, d = _metrics(spy_bh)
    print(f"| SPY buy&hold | {a*100:.1f} | {sh:.2f} | {d*100:.1f} |")

    for name, tickers in [("QQQ (single)", ["QQQ"]), ("MAG7", MAG7),
                          ("MAG5 (ex-TSLA)", MAG5), ("QQQ+MAG5", ["QQQ"] + MAG5)]:
        ret = run_basket(closes, tickers, start, end)
        a, v, sh, d = _metrics(ret)
        print(f"| {name} + fast-derisk | {a*100:.1f} | {sh:.2f} | {d*100:.1f} |")


if __name__ == "__main__":
    main()
