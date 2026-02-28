import sqlite3
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import os

# --- Configuration (matching drift_monitor.py) ---
DATABASE_PATH = 'sector_etfs.db'
DEFENSIVE_ASSETS = ['XLU', 'XLV', 'GLD']
MARKET_ASSET = 'SPY'
LOOKBACK_DAYS = 365  # Increased to get more historical context
ROLLING_CORRELATION_DAYS = 60
CONVERGENCE_THRESHOLD = 0.10
PLOT_OUTPUT = 'drift_analysis_plot.png'

def get_data_and_calculate_drift():
    if not os.path.exists(DATABASE_PATH):
        print(f"Error: Database {DATABASE_PATH} not found.")
        return None

    conn = sqlite3.connect(DATABASE_PATH)
    
    # Fetch all tickers needed
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

    # Pivot and calculate returns
    prices = df.pivot(index='date', columns='ticker', values='adj_close')
    returns = prices.pct_change().dropna()

    # Calculate rolling correlations for each defensive asset vs market
    drift_data = pd.DataFrame(index=returns.index)
    
    individual_corrs = []
    for asset in DEFENSIVE_ASSETS:
        col_name = f'{asset}_corr'
        drift_data[col_name] = returns[asset].rolling(window=ROLLING_CORRELATION_DAYS).corr(returns[MARKET_ASSET])
        individual_corrs.append(col_name)

    # Calculate average correlation across defensive assets
    drift_data['avg_correlation'] = drift_data[individual_corrs].mean(axis=1)
    
    return drift_data.dropna()

def plot_drift(drift_data):
    plt.figure(figsize=(12, 7))
    
    # Plot average correlation
    plt.plot(drift_data.index, drift_data['avg_correlation'], label='Avg Defensive Correlation', color='blue', linewidth=2)
    
    # Plot individual correlations with lower alpha
    for asset in DEFENSIVE_ASSETS:
        plt.plot(drift_data.index, drift_data[f'{asset}_corr'], label=f'{asset} Corr', alpha=0.3, linestyle='--')

    # Plot threshold
    plt.axhline(y=CONVERGENCE_THRESHOLD, color='red', linestyle='-', linewidth=1.5, label=f'Threshold ({CONVERGENCE_THRESHOLD})')
    
    # Formatting
    plt.title(f'Project Alpha: Style Drift Analysis ({ROLLING_CORRELATION_DAYS}-Day Rolling)', fontsize=14)
    plt.xlabel('Date', fontsize=12)
    plt.ylabel('Correlation to SPY', fontsize=12)
    plt.legend(loc='upper left')
    plt.grid(True, which='both', linestyle='--', alpha=0.5)
    
    # Highlight areas above threshold
    plt.fill_between(drift_data.index, drift_data['avg_correlation'], CONVERGENCE_THRESHOLD, 
                     where=(drift_data['avg_correlation'] > CONVERGENCE_THRESHOLD),
                     color='red', alpha=0.1, label='Drift Detected')

    plt.tight_layout()
    plt.savefig(PLOT_OUTPUT)
    print(f"Plot saved to {PLOT_OUTPUT}")

if __name__ == "__main__":
    data = get_data_and_calculate_drift()
    if data is not None:
        plot_drift(data)
    else:
        print("Failed to generate plot data.")
