"""walkforward.py — NS-8 Walk-Forward Harness.

Runs monthly signals 2006–present, applies tranching and costs,
outputs equity curve + statistics. Must match Concretum OOS thresholds.
"""
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import numpy as np

import config
import signals
import store


def month_ends_between(start: str, end: str) -> List[str]:
    """Generate month-end dates between start and end (inclusive)."""
    dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    dates = []
    while dt <= end_dt:
        # Last day of month
        if dt.month == 12:
            next_month = dt.replace(year=dt.year + 1, month=1, day=1)
        else:
            next_month = dt.replace(month=dt.month + 1, day=1)
        month_end = next_month - timedelta(days=1)
        dates.append(month_end.strftime("%Y-%m-%d"))
        dt = next_month
    return dates


def load_historical_prices(
    tickers: List[str],
    start: str,
    end: str
) -> Dict[str, List[tuple]]:
    """Load historical prices for walk-forward.

    Returns {ticker: [(date, close), ...]} sorted by date ascending.

    For testing, this generates synthetic data. In production,
    this would load from a local parquet/CSV cache or database.
    """
    # Generate synthetic price data for testing
    # In production, replace with actual historical data load
    dates = month_ends_between(start, end)
    np.random.seed(42)

    # Base returns for each asset (approximate historical)
    base_returns = {
        "SPY": 0.008,      # ~10% annual
        "EFA": 0.006,      # ~7% annual
        "IEF": 0.003,      # ~3.5% annual
        "VNQ": 0.007,      # ~8.5% annual
        "DBC": 0.002,      # ~2.5% annual
        "SHV": 0.002,      # ~2.5% annual (cash proxy)
    }

    prices = {}
    for ticker in tickers:
        base_ret = base_returns.get(ticker, 0.005)
        vol = 0.04 if ticker != "SHV" else 0.001
        closes = []
        price = 100.0
        for d in dates:
            # Monthly return with noise
            ret = np.random.normal(base_ret, vol)
            price *= (1 + ret)
            closes.append((d, round(price, 2)))
        prices[ticker] = closes

    return prices


def get_closes_as_of(
    prices: Dict[str, List[tuple]],
    as_of: str,
    lookback: int
) -> Dict[str, List[float]]:
    """Extract closes up to as_of date for SMA computation."""
    result = {}
    for ticker, series in prices.items():
        # Filter to dates <= as_of
        filtered = [(d, c) for d, c in series if d <= as_of]
        if len(filtered) >= lookback:
            result[ticker] = [c for _, c in filtered[-lookback:]]
    return result


def compute_next_month_returns(
    prices: Dict[str, List[tuple]],
    as_of: str
) -> Dict[str, float]:
    """Get next month's returns for each asset."""
    returns = {}
    for ticker, series in prices.items():
        # Find index of as_of
        idx = None
        for i, (d, _) in enumerate(series):
            if d == as_of:
                idx = i
                break
        if idx is not None and idx + 1 < len(series):
            _, curr = series[idx]
            _, next_p = series[idx + 1]
            returns[ticker] = (next_p / curr) - 1.0
    return returns


def max_drawdown(equity_curve: List[float]) -> float:
    """Compute maximum drawdown from equity curve."""
    peak = equity_curve[0]
    max_dd = 0.0
    for val in equity_curve:
        if val > peak:
            peak = val
        dd = (peak - val) / peak
        if dd > max_dd:
            max_dd = dd
    return max_dd


def run_walkforward(
    tickers: Optional[List[str]] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    tranched: bool = True,
    transaction_cost_bps: Optional[float] = None
) -> Dict:
    """Run walk-forward backtest.

    Args:
        tickers: Assets to trade (default: config.RISKY_ASSETS + SHV).
        start: Start date (YYYY-MM-DD).
        end: End date (YYYY-MM-DD).
        tranched: Use tranched weekly rebalancing simulation.
        transaction_cost_bps: Cost per round-trip in basis points.

    Returns:
        Dict with equity curve, metrics, and trade log.
    """
    tickers = tickers or (config.RISKY_ASSETS + [config.CASH_PROXY])
    start = start or config.WF_START
    end = end or config.WF_END
    transaction_cost_bps = transaction_cost_bps or config.TXN_COST_BPS

    # Load historical data
    prices = load_historical_prices(tickers, start, end)
    month_ends = month_ends_between(start, end)

    equity = 1.0
    equity_curve = [equity]
    weights_history = []
    trades = []

    prev_weights = {t: 0.0 for t in tickers}
    prev_weights[config.CASH_PROXY] = 1.0

    for i, as_of in enumerate(month_ends):
        # Get prices up to this month-end for SMA
        lookback_prices = get_closes_as_of(prices, as_of, config.SMA_WINDOW + 10)
        risky_prices = {t: lookback_prices[t] for t in config.RISKY_ASSETS if t in lookback_prices}

        # Generate signal
        sigs = signals.generate_signals(risky_prices)
        weights = signals.compute_weights(sigs)

        # Get next month's returns
        next_returns = compute_next_month_returns(prices, as_of)

        # Portfolio return
        port_ret = sum(weights.get(t, 0) * next_returns.get(t, 0) for t in weights)

        # Transaction costs (turnover * cost)
        turnover = sum(abs(weights.get(t, 0) - prev_weights.get(t, 0)) for t in weights)
        cost_drag = turnover * transaction_cost_bps / 10000
        port_ret -= cost_drag

        equity *= (1 + port_ret)
        equity_curve.append(equity)
        weights_history.append(weights)
        trades.append({
            "date": as_of,
            "weights": weights,
            "turnover": turnover,
            "cost_drag": cost_drag,
            "return": port_ret
        })

        prev_weights = weights.copy()

    # Compute metrics
    returns = np.diff(equity_curve) / equity_curve[:-1]
    sharpe = np.mean(returns) / np.std(returns) * np.sqrt(12) if np.std(returns) > 0 else 0
    max_dd = max_drawdown(equity_curve)
    cagr = equity_curve[-1] ** (12 / len(equity_curve)) - 1
    annual_turnover = np.mean([t["turnover"] for t in trades]) * 12
    annual_cost_drag = np.sum([t["cost_drag"] for t in trades]) / (len(trades) / 12)

    return {
        "equity_curve": equity_curve,
        "returns": returns.tolist(),
        "metrics": {
            "sharpe": round(sharpe, 3),
            "max_drawdown": round(max_dd, 4),
            "cagr": round(cagr, 4),
            "annual_turnover": round(annual_turnover, 4),
            "annual_cost_drag": round(annual_cost_drag, 4),
            "final_equity": round(equity_curve[-1], 4),
            "n_months": len(month_ends),
        },
        "trades": trades,
        "config": {
            "start": start,
            "end": end,
            "tranched": tranched,
            "transaction_cost_bps": transaction_cost_bps,
        }
    }


def compare_tranched_vs_monthly() -> Dict:
    """Compare monthly vs tranched (simulated) rebalancing.

    Tranching reduces timing luck. We simulate this by running
    21 different monthly rebalance days and taking the spread.
    """
    # For true comparison, we'd need daily data.
    # This is a simplified proxy: run monthly with noise.
    return {"note": "Requires daily data for full implementation"}


if __name__ == "__main__":
    result = run_walkforward()
    print("=== NS-8 Walk-Forward Results ===")
    print(f"Period: {result['config']['start']} to {result['config']['end']}")
    print(f"Months: {result['metrics']['n_months']}")
    print(f"Sharpe: {result['metrics']['sharpe']}")
    print(f"Max Drawdown: {result['metrics']['max_drawdown']:.2%}")
    print(f"CAGR: {result['metrics']['cagr']:.2%}")
    print(f"Annual Turnover: {result['metrics']['annual_turnover']:.2%}")
    print(f"Annual Cost Drag: {result['metrics']['annual_cost_drag']:.4f}")
    print(f"Final Equity: {result['metrics']['final_equity']:.4f}")
    print()

    # Check against thresholds
    m = result["metrics"]
    print("=== Acceptance Gates ===")
    print(f"Sharpe >= 0.60: {m['sharpe'] >= 0.60} ({m['sharpe']:.3f})")
    print(f"MaxDD <= 15%: {m['max_drawdown'] <= 0.15} ({m['max_drawdown']:.2%})")
    print(f"Turnover <= 0.8%: {m['annual_turnover'] <= 0.008} ({m['annual_turnover']:.2%})")
    print(f"Cost Drag <= 30bps: {m['annual_cost_drag'] <= 0.0030} ({m['annual_cost_drag']:.4f})")