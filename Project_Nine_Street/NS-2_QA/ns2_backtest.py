#!/usr/bin/env python3
"""
NS-2 Walk-Forward Backtest Harness (Phase 1)
=============================================
Honest out-of-sample evaluation of the HMM regime strategy.

Why this exists: the in-dashboard "backtest" fits the HMM on the full window and
Viterbi-decodes the whole sequence — every regime label sees the future, and the
strategy is then graded on the same data it was fit on. This harness removes both
biases:

  1. WALK-FORWARD: fit scaler + HMM on a trailing TRAIN window only, freeze them,
     then step through the next TEST window day by day.
  2. CAUSAL INFERENCE: regime at test day t is decoded from data up to and
     including t ONLY (frozen model, prefix decode → last state). No future bars.
  3. COSTS: transaction costs applied on every change in effective position.
  4. HONEST METRICS: per-trade round trips (long & short), profit factor,
     OOS Sharpe, max DD, and side-by-side in-sample comparison to expose inflation.

Usage:
  python3 ns2_backtest.py                          # default: MAG7 + TLT, MU
  python3 ns2_backtest.py --tickers TLT MU NVDA
  python3 ns2_backtest.py --years 4 --train 378 --test 21 --cost-bps 10

Output: markdown report to stdout + JSON at ns2_walkforward_results.json
This script is READ-ONLY with respect to the QA server (imports its functions,
never mutates its files or cache).
"""

import argparse
import json
import os
import sys
import warnings
from datetime import datetime

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qa_server as ns2  # reuse features / signals / labels — single source of truth

import yfinance as yf
from sklearn.preprocessing import StandardScaler


# ── Data ─────────────────────────────────────────────────────────────────────

def fetch_history(ticker, years):
    df = yf.Ticker(ticker).history(period=f"{years}y", interval="1d", auto_adjust=True)
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.columns = ["open", "high", "low", "close", "volume"]
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.dropna(inplace=True)
    return df


# ── Causal regime inference ──────────────────────────────────────────────────

def fit_frozen_hmm(train_feats):
    """Fit scaler + single HMM on the train window only. Returns (scaler, model, mapping)."""
    scaler = StandardScaler()
    X = scaler.fit_transform(train_feats.values)
    model = ns2._fit_single_hmm(X, random_state=42)
    mapping = ns2._label_hmm_states(model)
    return scaler, model, mapping


def causal_regimes(scaler, model, mapping, feats_upto_t):
    """
    Regime for the LAST row of feats_upto_t using only data ≤ t.
    Prefix Viterbi with a frozen model: the terminal state is causal.
    """
    X = scaler.transform(feats_upto_t.values)
    states = model.predict(X)
    return mapping[int(states[-1])]


# ── Walk-forward engine ──────────────────────────────────────────────────────

def walk_forward(ticker, years=4, train_len=378, test_len=21, cost_bps=10.0,
                 use_hmm=True, verbose=False):
    """
    Returns dict of OOS metrics, or {'error': ...}.
    train_len 378 ≈ 18 months of bars; test_len 21 ≈ 1 month. Roll monthly.
    """
    try:
        raw = fetch_history(ticker, years)
    except Exception as e:
        return {"ticker": ticker, "error": f"fetch failed: {e}"}

    if len(raw) < train_len + test_len + 80:
        return {"ticker": ticker, "error": f"only {len(raw)} bars; need ≥ {train_len + test_len + 80}"}

    df = ns2.add_rich_features(raw)
    feats = df[ns2.FEATURE_COLS].dropna()
    feat_index = feats.index
    df = df.loc[feat_index]  # align
    profile = ns2.get_profile(ticker)  # Phase 2: asset-class thresholds

    n = len(df)
    oos_regime = pd.Series(index=df.index, dtype="float64")

    # Roll: [start, start+train_len) trains; [start+train_len, +test_len) is OOS
    start = 0
    refits = 0
    while start + train_len < n:
        tr = feats.iloc[start : start + train_len]
        te_end = min(start + train_len + test_len, n)

        frozen = None
        if use_hmm:
            try:
                frozen = fit_frozen_hmm(tr)
                refits += 1
            except Exception:
                frozen = None

        for t in range(start + train_len, te_end):
            if frozen is not None:
                # causal: frozen model, data from train start through day t only
                window = feats.iloc[start : t + 1]
                oos_regime.iloc[t] = causal_regimes(*frozen, window)
            else:
                # rule-based fallback is already causal (trailing 20d window)
                sub = df.iloc[: t + 1]
                oos_regime.iloc[t] = ns2.assign_regimes_rule_based(sub, profile=profile)[-1]
        start += test_len

    oos_mask = oos_regime.notna()
    oos = df.loc[oos_mask].copy()
    regimes = oos_regime.loc[oos_mask].astype(int).values
    if len(oos) < 60:
        return {"ticker": ticker, "error": f"only {len(oos)} OOS bars"}

    regimes = ns2.apply_adaptive_persistence(regimes, oos, profile=profile)
    oos["regime"] = regimes
    oos["regime_confidence"] = 1.0

    # Signals + stops via the SAME production code path (macro=0: no look-ahead macro)
    oos = ns2.generate_signals_v2(oos, regimes, np.ones(len(oos)), None, None, 0, profile=profile)
    oos = ns2.apply_stops(oos)

    # Returns with costs
    oos["daily_return"] = oos["close"].pct_change()
    pos = oos["effective_pos"].shift(1).fillna(0)
    turnover = (oos["effective_pos"] - oos["effective_pos"].shift(1)).abs().fillna(0)
    cost = turnover * (cost_bps / 1e4)
    oos["strategy_return"] = pos * oos["daily_return"] - cost
    oos["cumulative_strat"] = (1 + oos["strategy_return"].fillna(0)).cumprod()
    oos["cumulative_bah"] = (1 + oos["daily_return"].fillna(0)).cumprod()
    oos["equity"] = 100_000 * oos["cumulative_strat"]

    m = compute_metrics(oos, ticker, cost_bps)
    m["oos_bars"] = int(len(oos))
    m["refits"] = refits
    m["mode"] = "hmm" if use_hmm else "rule"

    # In-sample comparison (the dashboard's flawed method) to expose inflation
    try:
        ins_regimes, agree, _, _ = ns2.get_regimes(df, use_hmm=use_hmm, profile=profile)
        ins = ns2.generate_signals_v2(df.copy(), ins_regimes, agree, None, None, 0, profile=profile)
        ins = ns2.apply_stops(ins)
        ins["daily_return"] = ins["close"].pct_change()
        ins["strategy_return"] = ins["effective_pos"].shift(1) * ins["daily_return"]
        ins_ret = float((1 + ins["strategy_return"].fillna(0)).prod() - 1)
        m["insample_return_pct"] = round(ins_ret * 100, 2)
    except Exception:
        m["insample_return_pct"] = None

    if verbose:
        dist = pd.Series(regimes).value_counts(normalize=True).round(3).to_dict()
        m["regime_dist"] = {ns2.REGIME_META[k]["label"]: v for k, v in dist.items()}
    return m


def compute_metrics(bt, ticker, cost_bps):
    strat = bt["strategy_return"].dropna()
    total = float(bt["cumulative_strat"].iloc[-1] - 1)
    bah = float(bt["cumulative_bah"].iloc[-1] - 1)
    ann_ret = (1 + total) ** (252 / max(len(strat), 1)) - 1
    ann_vol = float(strat.std() * np.sqrt(252))
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0.0

    roll_max = bt["equity"].cummax()
    max_dd = float(((bt["equity"] - roll_max) / roll_max).min())

    # Round trips (long & short), price-based
    trades, pos, entry = [], 0, 0.0
    for i in range(len(bt)):
        sig = int(np.sign(bt["signal"].iloc[i]))
        px = float(bt["close"].iloc[i])
        if sig != pos:
            if pos != 0 and entry > 0:
                trades.append((px - entry) / entry * pos)
            entry = px if sig != 0 else 0.0
            pos = sig
    if pos != 0 and entry > 0:
        trades.append((float(bt["close"].iloc[-1]) - entry) / entry * pos)

    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t <= 0]
    gross_w = sum(wins)
    gross_l = abs(sum(losses))
    pf = gross_w / gross_l if gross_l > 0 else (np.inf if gross_w > 0 else 0.0)

    return {
        "ticker": ticker,
        "oos_return_pct": round(total * 100, 2),
        "bah_return_pct": round(bah * 100, 2),
        "ann_return_pct": round(ann_ret * 100, 2),
        "sharpe": round(sharpe, 3),
        "max_dd_pct": round(max_dd * 100, 2),
        "n_trades": len(trades),
        "win_rate_pct": round(100 * len(wins) / max(len(trades), 1), 1),
        "profit_factor": round(pf, 2) if np.isfinite(pf) else None,
        "avg_win_pct": round(100 * np.mean(wins), 2) if wins else 0.0,
        "avg_loss_pct": round(100 * np.mean(losses), 2) if losses else 0.0,
        "cost_bps": cost_bps,
    }


# ── Report ───────────────────────────────────────────────────────────────────

GATE_PF = 1.5
GATE_SHARPE = 1.0

def verdict(m):
    if m.get("error"):
        return "ERROR"
    pf = m.get("profit_factor")
    # pf None = zero losing trades; with wins present that's a pass on PF,
    # gated by min trade count so 1-2 lucky trades can't sneak through.
    if pf is None:
        pf = np.inf if m.get("n_trades", 0) >= 3 and m.get("win_rate_pct", 0) > 0 else 0
    if pf >= GATE_PF and m["sharpe"] >= GATE_SHARPE:
        return "PASS"
    if pf >= 1.0:
        return "MARGINAL"
    return "NO-EDGE"


def main():
    ap = argparse.ArgumentParser(description="NS-2 walk-forward harness")
    ap.add_argument("--tickers", nargs="+",
                    default=list(ns2.MAG7.keys()) + ["TLT", "MU"])
    ap.add_argument("--years", type=int, default=4)
    ap.add_argument("--train", type=int, default=378)
    ap.add_argument("--test", type=int, default=21)
    ap.add_argument("--cost-bps", type=float, default=10.0)
    ap.add_argument("--rule-based", action="store_true", help="use rule-based regimes instead of HMM")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "ns2_walkforward_results.json"),
                    help="output JSON; default is the service dir so /api/backtest + acceptance gates see it")
    args = ap.parse_args()

    print(f"# NS-2 Walk-Forward Report — {datetime.now():%Y-%m-%d %H:%M}")
    print(f"mode={'rule' if args.rule_based else 'hmm'} | history={args.years}y | "
          f"train={args.train} bars | test={args.test} bars | cost={args.cost_bps}bps | "
          f"gates: PF≥{GATE_PF}, Sharpe≥{GATE_SHARPE}\n")

    results = []
    for t in args.tickers:
        print(f"  running {t} ...", flush=True)
        m = walk_forward(t, years=args.years, train_len=args.train, test_len=args.test,
                         cost_bps=args.cost_bps, use_hmm=not args.rule_based, verbose=True)
        m["verdict"] = verdict(m)
        results.append(m)

    ok = [m for m in results if "error" not in m]
    print("\n| Ticker | OOS Ret% | B&H% | InSample% | Sharpe | MaxDD% | Trades | Win% | PF | Verdict |")
    print("|--------|----------|------|-----------|--------|--------|--------|------|----|---------|")
    for m in results:
        if "error" in m:
            print(f"| {m['ticker']} | — | — | — | — | — | — | — | — | ERROR: {m['error']} |")
            continue
        print(f"| {m['ticker']} | {m['oos_return_pct']} | {m['bah_return_pct']} | "
              f"{m.get('insample_return_pct', '—')} | {m['sharpe']} | {m['max_dd_pct']} | "
              f"{m['n_trades']} | {m['win_rate_pct']} | {m['profit_factor']} | **{m['verdict']}** |")

    if ok:
        print(f"\nAggregate (n={len(ok)}): "
              f"avg OOS ret {np.mean([m['oos_return_pct'] for m in ok]):.2f}% | "
              f"avg Sharpe {np.mean([m['sharpe'] for m in ok]):.2f} | "
              f"avg win rate {np.mean([m['win_rate_pct'] for m in ok]):.1f}% | "
              f"pass rate {sum(1 for m in ok if m['verdict']=='PASS')}/{len(ok)}")
        ins = [m for m in ok if m.get("insample_return_pct") is not None]
        if ins:
            gap = np.mean([m["insample_return_pct"] - m["oos_return_pct"] for m in ins])
            print(f"In-sample inflation: dashboard method overstates return by {gap:+.2f}pp on average.")

    with open(args.out, "w") as f:
        json.dump({"generated": datetime.now().isoformat(),
                   "config": vars(args), "results": results}, f, indent=2, default=str)
    print(f"\nJSON: {args.out}")


if __name__ == "__main__":
    main()
