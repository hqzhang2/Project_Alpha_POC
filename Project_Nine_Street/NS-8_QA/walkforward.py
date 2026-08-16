"""walkforward.py — NS-8 Walk-Forward Harness (R4 rewrite).

Runs the monthly signal on REAL daily closes 2006–present, simulates the
Concretum 4-tranche weekly rebalancing, applies transaction costs on actual
turnover, and outputs trustworthy metrics.

R4 fixes (2026-08-16):
- Replaces the synthetic `np.random.seed(42)` price generator with REAL daily
  closes cached from yfinance (`data/ns8_hist_closes.json`). No synthetic data.
- Simulates tranched-weekly rebalancing (4 tranches, one per week of the month)
  instead of a single monthly rebalance. No lookahead: the signal is computed at
  month-end M and applied by the tranches DURING month M+1.
- Applies TXN_COST_BPS on ACTUAL turnover at each tranche rebalance.
- Annualizes Sharpe from the real daily return series (sqrt(252)).

House rules preserved: fail-open (missing/insufficient history -> cash), no
lookahead, deterministic (seeded only in tests, never in the production path).
"""
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import config

DATA_DIR = Path(__file__).resolve().parent / "data"
HIST_PATH = DATA_DIR / "ns8_hist_closes.json"

TICKERS = config.RISKY_ASSETS + [config.CASH_PROXY]


# ── Data loading (real, cached) ──────────────────────────────────────────
def load_historical_prices(path: Optional[Path] = None) -> Tuple[Dict, List[str]]:
    """Load REAL daily closes from the cache.

    Returns ({ticker: {date: close}}, [dates...]). Fail-open: a ticker with no
    data at all is dropped with a warning, never fabricated.
    """
    path = path or HIST_PATH
    with open(path) as fh:
        data = json.load(fh)
    dates = data["dates"]
    closes = data["closes"]
    prices: Dict[str, Dict[str, float]] = {}
    for t in data["tickers"]:
        series = {}
        for i, d in enumerate(dates):
            v = closes[t][i]
            if v is not None:
                series[d] = float(v)
        if series:
            prices[t] = series
        else:
            print(f"WARNING: {t} has no data; dropped (fail-open)")
    return prices, dates


# ── Signal (validated SMA logic; no inverse-vol — that is R8) ────────────
def target_weights_on(date: str, prices: Dict[str, Dict[str, float]],
                      window: Optional[int] = None) -> Dict[str, float]:
    """Target weights for `date` from the 200-day SMA binary signal.

    Fixed 20% per in-trend asset (v1 construction) so R4 isolates harness
    correctness from R8's inverse-vol sizing change. Insufficient history ->
    cash (fail-open).
    """
    window = window or config.SMA_WINDOW
    sigs = {}
    for t in config.RISKY_ASSETS:
        up_to = [c for d, c in sorted(prices.get(t, {}).items()) if d <= date]
        if len(up_to) >= window:
            sma = sum(up_to[-window:]) / window
            sigs[t] = 1 if up_to[-1] > sma else 0
        else:
            sigs[t] = 0  # insufficient history -> cash
    weights = {t: (config.ASSET_WEIGHT if sigs[t] == 1 else 0.0)
               for t in config.RISKY_ASSETS}
    weights[config.CASH_PROXY] = round(1.0 - sum(weights.values()), 12)
    return weights


# ── Tranche scheduler ────────────────────────────────────────────────────
def tranche_dates_for_month(all_dates: List[str], month_ym: str,
                            n_tranches: int) -> List[str]:
    """Pick n_tranches rebalance dates in a calendar month (one per week).

    Splits the trading days of `month_ym` into n roughly-equal buckets and
    returns the FIRST trading day of each bucket. Returns fewer than n_tranches
    if the month has too few trading days.
    """
    month_days = [d for d in all_dates if d.startswith(month_ym)]
    if not month_days:
        return []
    n = len(month_days)
    picks = []
    for t in range(min(n_tranches, n)):
        idx = t * n // n_tranches
        if idx < n and month_days[idx] not in picks:
            picks.append(month_days[idx])
    return sorted(picks)


# ── Core walk-forward (day-based, no lookahead) ──────────────────────────
def run_walkforward(start: Optional[str] = None, end: Optional[str] = None,
                    tranched: bool = True,
                    transaction_cost_bps: Optional[float] = None) -> Dict:
    """Run the walk-forward on REAL daily data.

    Signal is computed at the LAST trading day of month M (using only data <=
    that day), and the portfolio transitions to it either in one step at the
    START of month M+1 (monthly) or across 4 weekly tranches during month M+1
    (tranched). No lookahead: month-M info is never traded within month M.
    """
    import numpy as np

    start = start or config.WF_START
    end = end or config.WF_END
    transaction_cost_bps = transaction_cost_bps or config.TXN_COST_BPS
    cost_per = transaction_cost_bps / 10000.0

    prices, _all_dates = load_historical_prices()
    all_dates = [d for d in _all_dates if start <= d <= end]

    # Build month -> last trading day (signal reference dates)
    months: Dict[str, str] = {}
    for d in all_dates:
        months[d[:7]] = d  # last wins = last trading day of the month
    month_list = sorted(months.items())  # [(ym, last_day), ...]

    # Daily returns per ticker: {ticker: {date: ret}}
    daily_rets: Dict[str, Dict[str, float]] = {}
    for t, series in prices.items():
        ordered = sorted(series.items())
        dr = {}
        for (d0, c0), (d1, c1) in zip(ordered, ordered[1:]):
            if c0 and c1:
                dr[d1] = (c1 / c0) - 1.0
        daily_rets[t] = dr

    # Determine, per day, the target weights that are "active" (set at the
    # previous month-end signal) and the rebalance transitions that occur.
    # We iterate day by day over all_dates, tracking current weights.
    equity = 1.0
    daily_log: List[float] = []   # every day's portfolio return (for Sharpe)
    trade_log: List[Dict] = []

    # Map each date -> the target weights active on it (from the prior month).
    # Build a plan: for each month M+1, target = signal at month-end M.
    # active_target[date] and rebalance_days[date] for month M+1.
    active_target: Dict[str, Dict[str, float]] = {}
    rebalance_days: Dict[str, List[str]] = {}
    for i, (ym, last_day) in enumerate(month_list):
        if i + 1 >= len(month_list):
            break  # no following month to trade into within the window
        nxt_ym = month_list[i + 1][0]
        target = target_weights_on(last_day, prices)
        nxt_days = [d for d in all_dates if d.startswith(nxt_ym)]
        for d in nxt_days:
            active_target[d] = target
        if tranched:
            rebalance_days[nxt_ym] = tranche_dates_for_month(all_dates, nxt_ym, config.TRANCHES)
        else:
            # monthly: single rebalance at first trading day of the month
            rebalance_days[nxt_ym] = [nxt_days[0]] if nxt_days else []

    # Any date with no active target (first month) is all-cash.
    current = {t: 0.0 for t in TICKERS}
    current[config.CASH_PROXY] = 1.0

    for d in all_dates:
        # day return under current weights
        ret = sum(current.get(t, 0.0) * daily_rets.get(t, {}).get(d, 0.0)
                  for t in TICKERS)
        equity *= (1 + ret)
        daily_log.append(ret)

        # rebalances scheduled for this day
        ym = d[:7]
        targets_here = rebalance_days.get(ym, [])
        target = active_target.get(d)
        if target is not None and d in targets_here:
            # this tranche (or full book if monthly) moves current -> target
            frac = (1.0 / config.TRANCHES) if tranched else 1.0
            moved = {t: (target.get(t, 0.0) - current.get(t, 0.0)) * frac
                     for t in TICKERS}
            turnover_move = sum(abs(v) for v in moved.values())
            cost = turnover_move * cost_per
            equity -= cost
            for t in TICKERS:
                current[t] = current.get(t, 0.0) + moved[t]
            trade_log.append({
                "date": d, "kind": "tranche" if tranched else "monthly",
                "turnover": round(turnover_move, 6),
                "cost_drag": round(cost, 8),
            })

    # ── Metrics (correct daily annualization) ────────────────────────────
    rets = np.asarray(daily_log, dtype=float)
    n = len(rets)
    if n == 0:
        return {"metrics": {}, "equity_curve": [equity], "trades": trade_log}
    ann_factor = 252.0
    mean_d = float(np.mean(rets))
    std_d = float(np.std(rets))
    sharpe = (mean_d / std_d * np.sqrt(ann_factor)) if std_d > 0 else 0.0
    years = n / ann_factor
    cagr = (equity ** (1.0 / years) - 1.0) if equity > 0 and years > 0 else 0.0
    max_dd = _max_drawdown_from_returns(rets)
    total_turnover = sum(t["turnover"] for t in trade_log)
    annual_turnover = total_turnover / years if years else 0.0
    total_cost = sum(t["cost_drag"] for t in trade_log)
    annual_cost_drag = total_cost / years if years else 0.0

    return {
        "equity_curve": _equity_curve_from_returns(rets),
        "returns": [round(float(x), 8) for x in rets],
        "metrics": {
            "sharpe": round(sharpe, 3),
            "max_drawdown": round(max_dd, 4),
            "cagr": round(cagr, 4),
            "annual_turnover": round(annual_turnover, 4),
            "annual_cost_drag": round(annual_cost_drag, 6),
            "final_equity": round(equity, 4),
            "n_trading_days": n,
            "years": round(years, 2),
        },
        "trades": trade_log,
        "config": {
            "start": start, "end": end,
            "tranched": tranched,
            "transaction_cost_bps": transaction_cost_bps,
        },
    }


def _equity_curve_from_returns(rets) -> List[float]:
    curve = [1.0]
    for r in rets:
        curve.append(curve[-1] * (1 + r))
    return curve


def _max_drawdown_from_returns(rets) -> float:
    peak = 1.0
    equity = 1.0
    max_dd = 0.0
    for r in rets:
        equity *= (1 + r)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


# ── CLI ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    for label, tr in [("tranched-weekly", True), ("monthly", False)]:
        res = run_walkforward(tranched=tr)
        m = res["metrics"]
        print(f"\n=== NS-8 Walk-Forward [{label}] ===")
        print(f"Period: {res['config']['start']} to {res['config']['end']}")
        print(f"Trading days: {m['n_trading_days']}  years: {m['years']}")
        print(f"Sharpe: {m['sharpe']}")
        print(f"Max Drawdown: {m['max_drawdown']:.2%}")
        print(f"CAGR: {m['cagr']:.2%}")
        print(f"Annual Turnover: {m['annual_turnover']:.2%}")
        print(f"Annual Cost Drag: {m['annual_cost_drag']:.6f}")
        print(f"Final Equity: {m['final_equity']:.4f}")

        print("\n=== Acceptance Gates ===")
        print(f"Sharpe >= 0.60: {m['sharpe'] >= 0.60} ({m['sharpe']:.3f})")
        print(f"MaxDD <= 15%: {m['max_drawdown'] <= 0.15} ({m['max_drawdown']:.2%})")
        print(f"Turnover <= 0.8% (tranched): {m['annual_turnover'] <= 0.008} ({m['annual_turnover']:.2%})")
        print(f"Cost Drag <= 30bps: {m['annual_cost_drag'] <= 0.0030} ({m['annual_cost_drag']:.4f})")
        iv = (m['cagr'] / m['sharpe']) if m['sharpe'] else float('inf')
        print(f"Implied vol (CAGR/Sharpe): {iv:.1%} (sane band 5%-15%)")
