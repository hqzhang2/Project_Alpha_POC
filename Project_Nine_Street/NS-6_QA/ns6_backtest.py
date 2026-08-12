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
for p in (os.path.join(_ROOT, "Project_Sequoia", "terminal"),
          _ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)
_NS6 = os.path.dirname(os.path.abspath(__file__))
if _NS6 in sys.path:
    sys.path.remove(_NS6)
sys.path.insert(0, _NS6)

import budget as budget_mod
import config as config_mod
import enforcement as enforcement_mod
import options as options_mod
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
VIX = "^VIX"


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
def _vix_regime(vix):
    """VIX-based regime proxy (Phase 2 only — NOT the NS-5 macro regime)."""
    if vix is None:
        return "R1"
    if vix < 18:
        return "R1"   # Expansion
    if vix < 28:
        return "R2"   # Overheating
    if vix < 35:
        return "R3"   # Recession
    return "R4"       # Stagflation


def _ns5_target_weights(closes, tickers, as_of, lookback=504, method="tangency"):
    """NS-5 frontier target weights via closed-form frontier solutions.

    compute_frontier() returns the frontier CURVE but NOT the weight vector,
    so we replicate NS-5's methodology: Ledoit-Wolf shrunk covariance +
    closed-form solution, clipped ≥0, normalized. Causal (data ≤ as_of only).

    method:
      "tangency" — max-Sharpe portfolio w = inv(Σ)μ (return-sensitive).
      "gmv"      — global minimum variance w = inv(Σ)1/(1'inv(Σ)1)
                   (covariance-only, no expected-return estimates → more
                   robust, less concentrated).

    NOTE: Ledoit-Wolf is inlined here (not `from frontier import`) because
    NS-5's frontier.py imports NS-5 config/data_fetcher, whose `config`
    collides with NS-6's `config` on sys.path. Returns dict or None (fallback).

    Returns dict {ticker: weight} or None on failure (caller falls back).
    """
    from sklearn.covariance import LedoitWolf

    available = [t for t in tickers if t in closes.columns]
    if len(available) < 3:
        return None
    sub = closes[available][closes.index <= as_of].tail(lookback).copy()
    if len(sub) < 120:
        return None
    rets = sub.pct_change().dropna()
    if len(rets) < 60:
        return None
    mu = rets.mean().to_numpy() * 252.0
    cov = LedoitWolf().fit(rets.to_numpy()).covariance_ * 252.0
    # Closed-form weight vector per method.
    try:
        inv_cov = np.linalg.inv(cov)
        if method == "gmv":
            ones = np.ones(len(available))
            w = inv_cov @ ones / (ones @ inv_cov @ ones)
        else:  # tangency (max Sharpe)
            w = inv_cov @ mu
        w = np.clip(w, 0, None)
        s = w.sum()
        if s <= 1e-9:
            return None
        w = w / s
    except np.linalg.LinAlgError:
        return None
    # Zero out near-zero weights (numerical noise) and renormalize.
    w = np.where(w < 1e-4, 0.0, w)
    s = w.sum()
    if s <= 0:
        return None
    w = w / s
    return {t: float(wi) for t, wi in zip(available, w) if wi > 0}


def _phase2_signals(valid, dates, day, theta):
    """Compute the 4 Phase 2 signals at rebalance day (causal, ≤ day)."""
    i = dates.get_loc(day)
    # VIX level + trend (5d SMA diff)
    vix_series = valid[VIX].dropna() if VIX in valid.columns else pd.Series(dtype=float)
    vix_upto = vix_series[vix_series.index <= day]
    if len(vix_upto) >= 6:
        vix_level = float(vix_upto.iloc[-1])
        sma5_now = float(vix_upto.iloc[-5:].mean())
        sma5_prev = float(vix_upto.iloc[-6:-1].mean())
        vix_trend = sma5_now - sma5_prev
    else:
        vix_level, vix_trend = None, None

    # Regime from VIX proxy
    regime = _vix_regime(vix_level)

    # Vol ratio: 60d trailing ann. vol / full-window ann. vol (SPY returns)
    spy_ret = valid[SPY].pct_change().dropna()
    spy_upto = spy_ret[spy_ret.index <= day]
    long_run_vol = float(spy_upto.std() * np.sqrt(252)) if len(spy_upto) > 60 else None
    trailing = spy_upto.iloc[-60:] if len(spy_upto) >= 60 else spy_upto
    trailing_vol = float(trailing.std() * np.sqrt(252)) if len(trailing) > 5 else None
    vol_ratio = (trailing_vol / long_run_vol) if (trailing_vol and long_run_vol) else None

    # Stock-bond correlation: SPY/TLT 60d rolling
    corr = None
    if VIX in valid.columns and "TLT" in valid.columns:
        sp = valid[SPY][valid[SPY].index <= day].iloc[-60:]
        tl = valid["TLT"][valid["TLT"].index <= day].iloc[-60:]
        if len(sp) >= 30 and len(tl) >= 30:
            r_sp = sp.pct_change().dropna()
            r_tl = tl.pct_change().dropna()
            j = pd.concat([r_sp, r_tl], axis=1).dropna()
            if len(j) >= 30:
                corr = float(j.iloc[:, 0].corr(j.iloc[:, 1]))

    return regime, vol_ratio, corr, vix_level, vix_trend


def _daily_fast_expo(closes, dates, theta=None, lag=None):
    """Precompute a daily exposure series from the VIX smile (v2 fast de-risk).

    Exposure on day t uses VIX close at t-lag (no lookahead). Crisis
    hysteresis state is carried across the WHOLE window (enter at crisis_in,
    exit at crisis_out, hold between — flat floor, never zero).

    Returns pd.Series (float exposure 0.30-1.00) indexed by dates.
    """
    theta = theta or config_mod.load_theta()
    fd = theta["fast_derisk"]
    lag = fd["lookback_lag"] if lag is None else lag
    vix = closes[VIX].dropna()
    vix_lag = vix.reindex(dates).shift(lag)
    crisis = False
    expo = []
    for d in dates:
        v = vix_lag.loc[d]
        cap, crisis = enforcement_mod.fast_derisk_exposure(v, crisis, theta)
        expo.append(cap)
    return pd.Series(expo, index=dates)


def simulate(closes, start, end, top_n=12, cost_bps=10.0, phase=1, weighting="equal",
             selector=None, weighter=None, fast_derisk=False):
    """Run quarterly rebalance with NS-6 exposure multiplier.

    phase=1: budget-only multiplier (compute_exposure_multiplier).
    phase=2: multi-signal v2 multiplier + protective put drag.
    weighting="equal": equal-weight the selection.
    weighting="ns5":   NS-5 frontier tangency (max-Sharpe) target weights.

    selector : optional callable(closes, day, top_n) -> list[str] equity tickers.
               Replaces select_stocks (used by experiments; default = screener).
    weighter : optional callable(closes, sel, day) -> dict {ticker: weight}.
               Replaces target_weights (used by experiments). May return None
               to fall back to equal-weight.
    fast_derisk : bool — v2 mode. Exposure varies DAILY from the VIX smile
               (floored crisis hysteresis) instead of the quarterly multiplier.
               This is the evidence-backed v2 mechanism: fast de-risking
               preserves growth return (Sharpe 0.96-0.98) vs slow quarterly.

    Returns dict: {years: {year: {...}}, trades_per_quarter, totals}.
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

    # v2 fast de-risking: precompute daily exposure series (VIX smile, floored
    # crisis hysteresis), used to scale equity vs sleeve per-day in the loop.
    daily_expo = _daily_fast_expo(closes, dates, theta) if fast_derisk else None

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

        # 1. Select stocks + fixed non-equity sleeve
        if selector is not None:
            picked = selector(closes, day, top_n)
            sel = [t for t in picked if t in valid.columns]
        else:
            sel = [t for t in select_stocks(as_of, top_n=top_n) if t in valid.columns]
        sel = sel + [t for t in NON_EQUITY if t in valid.columns]
        sel = list(dict.fromkeys(sel))  # dedupe, keep order

        # 2. Target weights across the selection (equity + non-equity sleeve).
        if weighter is not None:
            tgt = weighter(closes, sel, day) or target_weights(sel)
        elif weighting.startswith("ns5"):
            method = "gmv" if weighting == "ns5-gmv" else "tangency"
            tgt = _ns5_target_weights(valid, sel, day, method=method) or target_weights(sel)
        else:
            tgt = target_weights(sel)

        # 3. NS-6 exposure multiplier.
        spy_history = valid[SPY].iloc[: dates.get_loc(day) + 1]
        spy_dd = budget_mod.compute_spy_drawdown(spy_history.tolist())
        budget_pct = budget_mod.compute_budget(spy_dd, theta)
        cur_dd = budget_mod.compute_drawdown(spy_history.tolist())
        remaining = budget_mod.budget_remaining(cur_dd, budget_pct, theta)

        put_drag = 0.0  # daily drag applied during this segment (phase 2)
        call_yield = 0.0  # daily boost (phase 3 covered call proxy)
        tax_proxy = 0.0  # one-off drag on rebalance day (phase 3)
        if phase == 1:
            multiplier = enforcement_mod.compute_exposure_multiplier(remaining, theta)
        else:  # phase 2+: multi-signal v2 + put drag
            regime, vol_ratio, corr, vix_level, vix_trend = _phase2_signals(
                valid, dates, day, theta)
            multiplier = enforcement_mod.compute_exposure_multiplier_v2(
                remaining, regime, vol_ratio, corr, vix_level, vix_trend, theta)
            # Protective put drag when multiplier < gate and put recommended.
            put = options_mod.recommend_put_overlay(
                multiplier, 1_000_000, vix_level, theta)
            if put["recommended"] and put["estimated_annual_cost_pct"] > 0:
                put_drag = put["estimated_annual_cost_pct"] / 252.0

        if phase >= 3:
            # Covered call yield: daily boost when gate allows.
            cc = options_mod.covered_call_gate(multiplier, None, theta)
            if cc["allowed"]:
                call_yield = 0.04 * cc["overwrite_pct"] / 252.0

        # Apply multiplier to equity sleeve only (non-equity unchanged).
        # In fast_derisk mode, keep FULL target weights — the DAILY exposure
        # series (from _daily_fast_expo) scales equity vs sleeve per-day in
        # the return loop; the fixed quarterly multiplier is not applied here.
        eff_tgt = {}
        for t, w in tgt.items():
            eff_tgt[t] = w * (multiplier if (t not in NON_EQUITY and not fast_derisk) else 1.0)
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

        if phase >= 3 and chosen:
            # Tax proxy: drag on rebalance day ≈ SELL weight × 5% (no lot history).
            # fraction of NAV (weight_delta × 0.05), NOT dollars.
            tax_proxy = sum(abs(t["weight_delta"]) * 0.05
                            for t in chosen["trades"] if t["action"] == "SELL")

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
        # Split portfolio into equity and sleeve sub-weights for fast_derisk.
        eq_w = {t: w for t, w in portfolio.items() if t not in NON_EQUITY}
        ne_w = {t: w for t, w in portfolio.items() if t in NON_EQUITY}
        eq_tot = sum(eq_w.values())
        ne_tot = sum(ne_w.values())
        for j in range(start_i, end_i + 1):
            if fast_derisk:
                # Daily exposure scales equity vs sleeve: ret = expo*eq + (1-expo)*ne
                expo = daily_expo.iloc[j]
                eq_r = (sum(eq_w.get(t, 0) * seg_ret[t].iloc[j - start_i] for t in eq_w)
                        / eq_tot if eq_tot else 0.0)
                ne_r = (sum(ne_w.get(t, 0) * seg_ret[t].iloc[j - start_i] for t in ne_w)
                        / ne_tot if ne_tot else 0.0)
                r = expo * eq_r + (1.0 - expo) * ne_r
            else:
                wts = {t: w for t, w in portfolio.items() if t in seg_ret.columns}
                r = sum(wts.get(t, 0) * seg_ret[t].iloc[j - start_i] for t in wts)
            if j == start_i:
                r -= cost  # pay trade cost on rebalance day
            if put_drag > 0:
                r -= put_drag  # protective put premium (phase 2)
            if call_yield > 0:
                r += call_yield  # covered call income (phase 3)
            if j == start_i and tax_proxy > 0:
                r -= tax_proxy  # tax drag on rebalance day (phase 3)
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
        "daily_port_ret": daily_port_ret,
        "daily_spy_ret": daily_spy_ret,
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
    ap.add_argument("--weighting", choices=["equal", "ns5", "ns5-gmv"], default="equal",
                    help="target-weight method: equal-weight, NS-5 tangency, or NS-5 GMV")
    ap.add_argument("--compare-weighting", action="store_true",
                    help="run both equal and ns5 weighting side-by-side (uses --phase)")
    ap.add_argument("--phase", type=int, default=2, choices=[1, 2, 3])
    ap.add_argument("--fast-derisk", action="store_true",
                    help="v2: daily VIX-smile exposure (floored crisis hysteresis), "
                         "not the quarterly budget multiplier")
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
    base = fetch_prices([SPY, VIX] + CANDIDATES, args.years, force=args.force_fetch)
    universe = build_universe(base, args.start, args.end, top_n=args.top_n)
    # fetch_prices merges missing picks into the cache internally
    closes = fetch_prices([SPY, VIX] + universe, args.years)
    print(f"  universe: {len(universe)} names, {len(closes.columns)} series "
          f"({len(closes)} days)")

    if args.compare_weighting:
        r1 = simulate(closes, args.start, args.end, top_n=args.top_n,
                      cost_bps=args.cost_bps, phase=args.phase, weighting="equal")
        r2 = simulate(closes, args.start, args.end, top_n=args.top_n,
                      cost_bps=args.cost_bps, phase=args.phase, weighting="ns5")
        r3 = simulate(closes, args.start, args.end, top_n=args.top_n,
                      cost_bps=args.cost_bps, phase=args.phase, weighting="ns5-gmv")
        _report_weighting([r1, r2, r3], ["equal", "tangency", "gmv"],
                          args, phase_label=f"Phase {args.phase}")
        return

    print(f"  simulating Phase {args.phase} (weighting={args.weighting}, "
          f"fast_derisk={args.fast_derisk})...", flush=True)
    results = simulate(closes, args.start, args.end, top_n=args.top_n,
                       cost_bps=args.cost_bps, phase=args.phase,
                       weighting=args.weighting, fast_derisk=args.fast_derisk)
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
        f.write(f"# NS-6 Walk-Forward Backtest (P{args.phase}, {args.weighting}) — {datetime.now():%Y-%m-%d}\n\n")
        f.write(f"## {'PASS' if passed else 'FAIL'}\n")
        for k, v in gate.items():
            f.write(f"- {k}: {v}\n")
        f.write(f"\n```json\n{json.dumps(results, indent=2, default=str)}\n```\n")
    print(f"Report: {REPORT}")


def _report_weighting(results, labels, args, phase_label="Phase 2"):
    """Side-by-side weighting report (2+ variants) for a fixed phase."""
    gates = [evaluate(r)[1] for r in results]
    print(f"\n## {phase_label}: weighting comparison")
    for lbl, g in zip(labels, gates):
        print(f"  {lbl:8} {'PASS' if g['passed'] else 'FAIL'} — excess {g['excess_positive_years']}/{g['n_years']}, "
              f"DD halved {g['dd_halved_years']}/{g['n_years']}, trades/qtr {g['avg_trades_per_quarter']:.1f}")

    hdr = "| Metric |" + "|".join(f" {l} |" for l in labels)
    print("\n" + hdr)
    print("|" + "|".join("--------" for _ in labels) + "|")
    metric_defs = [
        ("total port ret%", lambda r: r['total_port_ret']*100),
        ("total SPY ret%", lambda r: r['total_spy_ret']*100),
        ("port max DD%", lambda r: r['port_max_dd']*100),
        ("DD ratio", lambda r: r['dd_ratio']),
        ("excess pos yrs", lambda r: None),
        ("DD halved yrs", lambda r: None),
        ("trades/qtr", lambda r: r['avg_trades_per_quarter']),
    ]
    for name, fn in metric_defs:
        vals = []
        for r, g in zip(results, gates):
            if name.startswith("excess pos"):
                vals.append(g['excess_positive_years'])
            elif name.startswith("DD halved"):
                vals.append(g['dd_halved_years'])
            else:
                vals.append(fn(r))
        print(f"| {name} |" + "|".join(f" {v:.1f} " if isinstance(v, float) else f" {v} " for v in vals) + "|")

    out = {"generated": datetime.now().isoformat(), "config": vars(args),
           "weightings": {lbl: {"results": r, "gate": g}
                          for lbl, r, g in zip(labels, results, gates)}}
    with open(args.out, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nJSON: {args.out}")


if __name__ == "__main__":
    main()
