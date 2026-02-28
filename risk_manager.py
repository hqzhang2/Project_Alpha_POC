import sqlite3
import pandas as pd
import numpy as np
import os

# --- Configuration (from PROJECT_PROFILE.md) ---
DATABASE_PATH = 'sector_etfs.db'
# Added XLP for comparison as a potential XLV replacement
TICKERS = ['XLU', 'XLV', 'GLD', 'XLP', 'SPY']
DEFENSIVE_ASSETS = ['XLU', 'XLV', 'GLD', 'XLP']
MARKET_ASSET = 'SPY'
ROLLING_WINDOW = 60
VaR_CONFIDENCE = 0.95
BASE_WEIGHT = 0.25 # Assuming equal weight for defensive sleeve as a starting point

def calculate_var(returns, window=60):
    """Calculates Historical Value at Risk (95%)."""
    return returns.rolling(window=window).apply(lambda x: np.percentile(x, (1-VaR_CONFIDENCE)*100))

def calculate_drift_adjusted_weight(base_weight, current_corr, target_corr=0.10):
    """
    Refined Weighting Formula:
    Reduces weight based on correlation drift from target.
    Adjustment = Base * (1 - (Current Corr - Target Corr) * Sensitivity)
    """
    if current_corr <= target_corr:
        return base_weight
    
    # Sensitivity factor: how aggressively we cut weight as correlation rises
    sensitivity = 1.5 
    drift_delta = current_corr - target_corr
    reduction_factor = max(0, 1 - (drift_delta * sensitivity))
    
    return base_weight * reduction_factor

def run_risk_check_v2():
    if not os.path.exists(DATABASE_PATH):
        print(f"Error: Database {DATABASE_PATH} not found.")
        return

    conn = sqlite3.connect(DATABASE_PATH)
    ticker_list = ", ".join([f"'{t}'" for t in TICKERS])
    
    query = f"""
    SELECT date, ticker, adj_close
    FROM daily_prices
    WHERE ticker IN ({ticker_list})
    ORDER BY date ASC
    """
    df = pd.read_sql_query(query, conn, parse_dates=['date'])
    conn.close()

    if df.empty:
        print("No data found.")
        return

    prices = df.pivot(index='date', columns='ticker', values='adj_close')
    returns = prices.pct_change().dropna()

    print("\n" + "="*70)
    print(f" PROJECT ALPHA: REFINED RISK MANAGER & SECTOR SWAP ANALYSIS")
    print("="*70)
    latest_date = returns.index[-1]
    print(f"Analysis Date: {latest_date.strftime('%Y-%m-%d')}")
    print("-" * 70)

    results = []
    for asset in DEFENSIVE_ASSETS:
        # Calculate Rolling Correlation
        corr_series = returns[asset].rolling(window=ROLLING_WINDOW).corr(returns[MARKET_ASSET])
        curr_corr = corr_series.iloc[-1]
        
        # Calculate Adjusted Weight
        adj_weight = calculate_drift_adjusted_weight(BASE_WEIGHT, curr_corr)
        pct_reduction = (1 - (adj_weight / BASE_WEIGHT)) * 100
        
        status = "⚠️ DRIFT" if curr_corr > 0.15 else "✅ STABLE"
        results.append({
            'Asset': asset,
            'Corr': curr_corr,
            'Status': status,
            'AdjWeight': adj_weight,
            'Reduction': pct_reduction
        })

    # Display Results
    for res in results:
        print(f"{res['Asset']} | Corr: {res['Corr']:.4f} | {res['Status']} | Sugg. Weight: {res['AdjWeight']:.2%} (-{res['Reduction']:.1f}%)")

    print("-" * 70)
    
    # Sector Swap Recommendation
    xlv = next(r for r in results if r['Asset'] == 'XLV')
    xlp = next(r for r in results if r['Asset'] == 'XLP')
    
    print("\nSector Swap Analysis (XLV vs XLP):")
    print(f"XLV Correlation: {xlv['Corr']:.4f}")
    print(f"XLP Correlation: {xlp['Corr']:.4f}")
    
    if xlp['Corr'] < xlv['Corr'] - 0.05:
        print(f"RECOMMENDATION: SWAP XLV -> XLP.")
        print(f"XLP currently offers {((xlv['Corr'] - xlp['Corr'])/xlv['Corr'])*100:.1f}% better diversification to SPY.")
    else:
        print("RECOMMENDATION: MAINTAIN XLV (XLP correlation profile is similar).")
    
    print("="*70)

if __name__ == "__main__":
    run_risk_check_v2()
