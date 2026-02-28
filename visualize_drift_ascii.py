import sqlite3
import pandas as pd
import numpy as np
import os

# --- Configuration (matching drift_monitor.py) ---
DATABASE_PATH = 'sector_etfs.db'
DEFENSIVE_ASSETS = ['XLU', 'XLV', 'GLD']
MARKET_ASSET = 'SPY'
LOOKBACK_DAYS = 365
ROLLING_CORRELATION_DAYS = 60
CONVERGENCE_THRESHOLD = 0.10

def get_data_and_calculate_drift():
    if not os.path.exists(DATABASE_PATH):
        print(f"Error: Database {DATABASE_PATH} not found.")
        return None

    conn = sqlite3.connect(DATABASE_PATH)
    tickers = DEFENSIVE_ASSETS + [MARKET_ASSET]
    ticker_list = ", ".join([f"'{t}'" for t in tickers])
    
    query = f"""
    SELECT date, ticker, adj_close
    FROM daily_prices
    WHERE ticker IN ({ticker_list})
    ORDER BY date ASC
    """
    
    df = pd.read_sql_query(query, conn, parse_dates=['date'])
    conn.close()

    if df.empty:
        print("No data found in database.")
        return None

    prices = df.pivot(index='date', columns='ticker', values='adj_close')
    returns = prices.pct_change().dropna()

    drift_data = pd.DataFrame(index=returns.index)
    individual_corrs = []
    for asset in DEFENSIVE_ASSETS:
        col_name = f'{asset}_corr'
        drift_data[col_name] = returns[asset].rolling(window=ROLLING_CORRELATION_DAYS).corr(returns[MARKET_ASSET])
        individual_corrs.append(col_name)

    drift_data['avg_correlation'] = drift_data[individual_corrs].mean(axis=1)
    return drift_data.dropna()

def generate_ascii_plot(drift_data):
    # Print summary statistics
    print("\n" + "="*50)
    print(" PROJECT ALPHA: STYLE DRIFT SUMMARY (60-Day Rolling)")
    print("="*50)
    print(f"Latest Date: {drift_data.index[-1].strftime('%Y-%m-%d')}")
    print(f"Latest Avg Correlation: {drift_data['avg_correlation'].iloc[-1]:.4f}")
    print(f"Convergence Threshold:  {CONVERGENCE_THRESHOLD:.4f}")
    
    status = "⚠️ DRIFT DETECTED" if drift_data['avg_correlation'].iloc[-1] > CONVERGENCE_THRESHOLD else "✅ WITHIN TOLERANCE"
    print(f"Current Status:         {status}")
    print("-"*50)

    # Simplified ASCII Trend (last 30 data points)
    subset = drift_data.tail(30)
    print("\nRecent Trend (Avg Correlation to SPY):")
    
    min_val = min(subset['avg_correlation'].min(), CONVERGENCE_THRESHOLD) - 0.05
    max_val = max(subset['avg_correlation'].max(), CONVERGENCE_THRESHOLD) + 0.05
    
    for date, row in subset.iterrows():
        val = row['avg_correlation']
        # Normalize for bar length (max 40 chars)
        bar_len = int(((val - min_val) / (max_val - min_val)) * 40)
        marker = '!' if val > CONVERGENCE_THRESHOLD else '#'
        line = f"{date.strftime('%Y-%m-%d')} | {'=' * bar_len}{marker} ({val:.3f})"
        
        if abs(val - CONVERGENCE_THRESHOLD) < 0.01:
            line += " <--- THRESHOLD"
        print(line)

    print("\n(Legend: # = Normal, ! = Drifted)")
    print("="*50)

if __name__ == "__main__":
    data = get_data_and_calculate_drift()
    if data is not None:
        generate_ascii_plot(data)
    else:
        print("Failed to generate data.")
