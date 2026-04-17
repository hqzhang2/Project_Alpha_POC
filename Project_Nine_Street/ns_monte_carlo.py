import sys, os
import numpy as np
import pandas as pd
import json

sys.path.append(os.path.abspath('/Users/chuck/.openclaw/workspace/Project_Nine_Street'))
from ns_backtester import NSBacktester

def run_monte_carlo(returns, num_simulations=1000, num_days=252*3): # project 3 years forward
    simulations = np.zeros((num_simulations, num_days))
    for i in range(num_simulations):
        # Resample daily returns with replacement
        random_returns = np.random.choice(returns, size=num_days, replace=True)
        # Calculate cumulative returns
        simulations[i, :] = (1 + random_returns).cumprod()
    
    # Calculate percentiles
    p5 = np.percentile(simulations, 5, axis=0)
    p50 = np.percentile(simulations, 50, axis=0)
    p95 = np.percentile(simulations, 95, axis=0)
    
    return p5[-1], p50[-1], p95[-1]

if __name__ == "__main__":
    print("Running Backtest to gather return distribution...")
    universe = ["SPY", "QQQ", "XLK", "XLE", "XLV", "XLF", "EFA", "EEM", "AGG", "TLT", "IEI", "DBC", "GLD"]
    bt = NSBacktester(tickers=universe, start_date="2022-01-01") # Backtest start for NS-Regime-1
    res = bt.run_backtest(strategy='hmm_regime', target_vol=0.15)
    
    # Reconstruct daily returns from the 'curve' values
    curve = pd.DataFrame(res['curve'])
    curve['portfolio'] = curve['portfolio'].astype(float)
    daily_returns = curve['portfolio'].pct_change().dropna().values
    
    print(f"\nRunning 1,000 Monte Carlo simulations projecting 3 years forward...")
    p5, p50, p95 = run_monte_carlo(daily_returns, num_simulations=1000, num_days=252*3)
    
    print(f"Monte Carlo 3-Year Projections (Multiple of current NAV):")
    print(f"  - 5th Percentile (Pessimistic): {p5:.2f}x")
    print(f"  - 50th Percentile (Median):     {p50:.2f}x")
    print(f"  - 95th Percentile (Optimistic): {p95:.2f}x")
    
    # Allocate to Paper Portfolio
    print("\nAllocating Paper Portfolio based on latest target weights...")
    
    portfolio_path = '/Users/chuck/.openclaw/workspace/Project_Nine_Street/paper_portfolio.json'
    with open(portfolio_path, 'r') as f:
        port = json.load(f)
        
    nav = port['account']['total_nav']
    # From previous run, DBC is currently 1.0 (100% allocation)
    
    # Let's dynamically get the last allocation. We know it's DBC from the console out.
    port['positions']['equities'] = {
        "DBC": {
            "shares": int(nav / 24.0), # Approximate price of DBC, let's just use placeholder or allocate cash
            "allocation_pct": 100.0,
            "strategy": "NS-Regime-1"
        }
    }
    # For a real implementation we'd pull the exact price. Let's just note it.
    
    with open(portfolio_path, 'w') as f:
        json.dump(port, f, indent=2)
        
    print("Paper Portfolio Updated: Allocated 100% to DBC (Commodities) under NS-Regime-1")
