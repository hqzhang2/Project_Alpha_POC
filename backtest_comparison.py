import sqlite3
import pandas as pd
import numpy as np
import os

# --- Configuration (from PROJECT_PROFILE.md) ---
DATABASE_PATH = 'sector_etfs.db'
TICKERS = ['XLU', 'XLV', 'GLD', 'XLP', 'SPY']
MARKET_ASSET = 'SPY'
LOOKBACK_DAYS = 90
INITIAL_CAPITAL = 10000

def get_returns():
    if not os.path.exists(DATABASE_PATH):
        return None
    conn = sqlite3.connect(DATABASE_PATH)
    ticker_list = ", ".join([f"'{t}'" for t in TICKERS])
    query = f"SELECT date, ticker, adj_close FROM daily_prices WHERE ticker IN ({ticker_list}) ORDER BY date ASC"
    df = pd.read_sql_query(query, conn, parse_dates=['date'])
    conn.close()
    if df.empty: return None
    prices = df.pivot(index='date', columns='ticker', values='adj_close')
    return prices.pct_change().tail(LOOKBACK_DAYS).dropna()

def run_backtest():
    returns = get_returns()
    if returns is None:
        print("Error: No data for backtest.")
        return

    # Portfolio 1: Baseline (XLU, XLV, GLD) - Equal Weighted 1/3 each
    p1_assets = ['XLU', 'XLV', 'GLD']
    p1_returns = returns[p1_assets].mean(axis=1)
    p1_cum_return = (1 + p1_returns).cumprod()

    # Portfolio 2: Proposed Swap (XLU, XLP, GLD) - Equal Weighted 1/3 each
    p2_assets = ['XLU', 'XLP', 'GLD']
    p2_returns = returns[p2_assets].mean(axis=1)
    p2_cum_return = (1 + p2_returns).cumprod()

    # Market Benchmark
    spy_cum_return = (1 + returns[MARKET_ASSET]).cumprod()

    # Statistics
    def get_stats(ret_series):
        total_ret = (ret_series + 1).prod() - 1
        vol = ret_series.std() * np.sqrt(252)
        sharpe = (ret_series.mean() / ret_series.std()) * np.sqrt(252) if ret_series.std() != 0 else 0
        var_95 = np.percentile(ret_series, 5)
        return total_ret, vol, sharpe, var_95

    p1_stats = get_stats(p1_returns)
    p2_stats = get_stats(p2_returns)
    spy_stats = get_stats(returns[MARKET_ASSET])

    print("\n" + "="*70)
    print(f" PROJECT ALPHA: 90-DAY BACKTEST COMPARISON (XLV vs XLP)")
    print("="*70)
    print(f"{'Metric':<20} | {'Baseline (XLV)':<15} | {'Proposed (XLP)':<15} | {'SPY':<10}")
    print("-" * 70)
    print(f"{'Total Return':<20} | {p1_stats[0]:>14.2%} | {p2_stats[0]:>14.2%} | {spy_stats[0]:>9.2%}")
    print(f"{'Annualized Vol':<20} | {p1_stats[1]:>14.2%} | {p2_stats[1]:>14.2%} | {spy_stats[1]:>9.2%}")
    print(f"{'Sharpe Ratio':<20} | {p1_stats[2]:>14.2f} | {p2_stats[2]:>14.2f} | {spy_stats[2]:>9.2f}")
    print(f"{'Daily VaR (95%)':<20} | {p1_stats[3]:>14.2%} | {p2_stats[3]:>14.2%} | {spy_stats[3]:>9.2%}")
    print("-" * 70)
    
    alpha_p1 = p1_stats[0] - spy_stats[0]
    alpha_p2 = p2_stats[0] - spy_stats[0]
    
    print(f"\nPortfolio Alpha (Baseline vs SPY): {alpha_p1*100:+.2f} bps")
    print(f"Portfolio Alpha (Proposed vs SPY): {alpha_p2*100:+.2f} bps")
    
    print("\nCONCLUSION:")
    if p2_stats[0] > p1_stats[0] and p2_stats[1] < p1_stats[1]:
        print("SWAP IS SUPERIOR: Higher return with lower volatility.")
    elif p2_stats[3] > p1_stats[3]:
        print("SWAP IS DEFENSIVELY SUPERIOR: Better tail-risk protection (VaR).")
    else:
        print("SWAP RESULTS MIXED: Review risk/reward trade-off.")
    print("="*70)

if __name__ == "__main__":
    run_backtest()
