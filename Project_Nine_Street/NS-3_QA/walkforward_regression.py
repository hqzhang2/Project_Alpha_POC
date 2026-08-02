#!/usr/bin/env python3
"""
NS-3 Walk-Forward OOS Regression (validated signal check)
==========================================================
Asserts the 52-week momentum rank (Tier 1) clears base rate at 3M and 6M on
the 39-pair universe, walk-forward OOS (anchored folds 2021-2026).

This is the living regression test for the validated claim: cross-sectional
12M-lookback momentum beats base rate by ~14-15pp (56.5% vs 41.2% at 3M,
51.7% vs 37.6% at 6M on the pinned universe). If this fails, the signal
regression must be investigated before any threshold/rank change is trusted.

Data: reuses /tmp/wf2_cache_*.pkl when present (fast, deterministic);
otherwise fetches from yfinance (slow, needs network) and caches.

Usage:  python3 walkforward_regression.py [--fetch]
Exit:   0 = signal clears base rate (PASS), 1 = regression (FAIL)
"""
import argparse
import glob
import os
import pickle
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

SECTORS = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY", "XLC", "XLRE"]
STYLE = ["IWB", "IWM", "IWF", "IWD", "MDY", "SPSM"]
FACTORS = ["QUAL", "USMV", "MTUM", "SPLV", "VLUE", "SIZE"]
THEMATIC = ["QQQ", "DIA", "SMH", "XBI", "XRT", "KRE"]
INTL = ["EFA", "EEM"]
FIXED = ["TLT", "IEF", "HYG", "GLD", "LQD"]
WATCH = ["IBIT", "MAGS", "ARKK"]
PAIRS = list(dict.fromkeys([(s, "SPY") for s in SECTORS + STYLE + FACTORS + THEMATIC + INTL + FIXED + WATCH]))

FOLDS = [
    ("2020-12-31", "2021-01-01", "2022-12-31"),
    ("2022-12-31", "2023-01-01", "2024-12-31"),
    ("2024-12-31", "2025-01-01", "2026-12-31"),
]
MOM_WEEKS = 52          # validated lookback (12M)
MIN_MARGIN_PP = 5.0     # hit must clear base rate by >= this (pp)


def load_closes(fetch: bool):
    """Return (pairs: dict[t1->close], spy: Series)."""
    cache_files = sorted(glob.glob("/tmp/wf2_cache_*.pkl"))
    if cache_files and not fetch:
        pairs, spy = {}, None
        for f in cache_files:
            stem = f.replace("/tmp/wf2_cache_", "").replace(".pkl", "")
            t1, t2 = stem.split("_")[0], stem.split("_")[1]
            if t2 != "SPY":
                continue
            with open(f, "rb") as fh:
                df = pickle.load(fh)
            if spy is None or len(df["b"]) > len(spy):
                spy = df["b"]
            pairs[t1] = df["a"]
        return pairs, spy

    import yfinance as yf  # deferred: only needed for fresh fetch
    pairs, spy = {}, None
    for t1, t2 in PAIRS:
        a = yf.Ticker(t1).history(period="10y", auto_adjust=True)["Close"]
        b = yf.Ticker(t2).history(period="10y", auto_adjust=True)["Close"]
        df = pd.DataFrame({"a": a, "b": b}).dropna()
        if spy is None or len(df["b"]) > len(spy):
            spy = df["b"]
        pairs[t1] = df["a"]
        with open(f"/tmp/wf2_cache_{t1}_{t2}.pkl", "wb") as fh:
            pickle.dump(df, fh)
    return pairs, spy


def weekly_52w_rank_top3(closes, spy, dt, history):
    """12M (252 trading-day) ratio momentum rank; returns top-3 symbols.
    Matches the validated mom12 definition from validation A (pct_change(252))."""
    lo = history[-320:]
    mom = {}
    for s in closes:
        ser = (closes[s].reindex(lo).ffill() / spy.reindex(lo).ffill()).dropna()
        if len(ser) >= 253:
            v = ser.pct_change(252).iloc[-1]
            if np.isfinite(v):
                mom[s] = v
    if len(mom) < 4:
        return []
    return pd.Series(mom).sort_values(ascending=False).head(3).index.tolist()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="fetch fresh data instead of using cache")
    args = ap.parse_args()

    closes, spy = load_closes(args.fetch)
    idx = spy.index
    long_pairs = {s: p for s, p in closes.items() if len(p) >= 1000}
    for s in long_pairs:
        idx = idx.intersection(long_pairs[s].index)
    closes = {s: closes[s].reindex(idx).ffill() for s in closes}
    spy = spy.reindex(idx).ffill()
    print(f"universe: {len(closes)} pairs, grid {idx[0].date()}..{idx[-1].date()}")

    results = {}
    for H, label in ((63, "3M"), (126, "6M")):
        fwd = {s: (closes[s] / spy).shift(-H) / (closes[s] / spy) - 1 for s in closes}
        base_all, top3_hits = [], []
        for _, ts, te in FOLDS:
            weeks = idx[(idx >= ts) & (idx <= te)].to_series().resample("W-FRI").last().dropna()
            for dt in weeks:
                history = idx[idx <= dt]
                if len(history) < 53:
                    continue
                top3 = weekly_52w_rank_top3(closes, spy, dt, history)
                if len(top3) == 3:
                    vals = [fwd[s].get(dt) for s in top3]
                    vals = [v for v in vals if v is not None and np.isfinite(v)]
                    if len(vals) == 3:
                        top3_hits.append(float(np.mean(vals)))
                base_all += [v for s in closes if (v := fwd[s].get(dt)) is not None and np.isfinite(v)]

        ba = np.array(base_all)
        th = np.array(top3_hits)
        base_hit = (ba > 0).mean()
        sig_hit = (th > 0).mean() if len(th) else 0.0
        margin = (sig_hit - base_hit) * 100
        results[label] = (base_hit, sig_hit, margin, len(th))
        print(f"{label}: n={len(th):>4} signal hit={sig_hit:>6.1%}  base={base_hit:>6.1%}  "
              f"margin={margin:+.1f}pp  ({'PASS' if margin >= MIN_MARGIN_PP else 'FAIL'})")

    ok = all(m >= MIN_MARGIN_PP for _, _, m, _ in results.values())
    print(f"\nRESULT: {'PASS - 52w rank clears base rate' if ok else 'FAIL - signal regression'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
