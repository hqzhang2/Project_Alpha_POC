import psycopg2
import pandas as pd
import numpy as np

# --- Configuration ---
POSTGRES_CONN = "dbname=project_alpha user=chuck host=localhost"

# Portfolio Sector ETFs
SECTORS = ['XLE', 'XLU', 'XLP', 'GLD', 'XLF', 'XLC', 'XLI', 'XLB', 'XLRE', 'XLK', 'XLY']
BENCHMARK = 'SPY'

def get_data_pg(tickers, lookback=90):
    """Fetch lookback window data from PostgreSQL"""
    conn = psycopg2.connect(POSTGRES_CONN)
    ticker_str = "','".join(tickers + [BENCHMARK])
    query = f"""
    SELECT ticker, date, adj_close 
    FROM daily_prices 
    WHERE ticker IN ('{ticker_str}')
    ORDER BY date DESC
    LIMIT {(lookback + 1) * (len(tickers) + 1)}
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    # Pivot for time series analysis
    df = df.pivot(index='date', columns='ticker', values='adj_close')
    df = df.astype(float).dropna()
    return df

def calculate_drift(df):
    """Calculate current correlation drift relative to SPY"""
    returns = df.pct_change().dropna()
    correlations = returns.corr()[BENCHMARK].drop(BENCHMARK)
    avg_drift = correlations.mean()
    return correlations, avg_drift

if __name__ == "__main__":
    try:
        data = get_data_pg(SECTORS)
        corrs, avg_drift = calculate_drift(data)
        
        print(f"--- POSTGRESQL DRIFT MONITOR (90D) ---")
        print(f"Average Correlation to SPY: {avg_drift:.4f}")
        print("-" * 35)
        for ticker, corr in corrs.sort_values(ascending=False).items():
            print(f"{ticker:<5}: {corr:>8.4f}")
        
        if avg_drift > 0.15:
            print("\n⚠️ ALERT: Elevated Style Drift Detected.")
        else:
            print("\n✅ Status: Drift within acceptable limits.")
            
    except Exception as e:
        print(f"Drift Monitor Error: {e}")
