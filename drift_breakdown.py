import sqlite3
import pandas as pd
import os

# --- Configuration (matching PROJECT_PROFILE.md) ---
DATABASE_PATH = 'sector_etfs.db'
DEFENSIVE_ASSETS = ['XLU', 'XLV', 'GLD']
MARKET_ASSET = 'SPY'
ROLLING_CORRELATION_DAYS = 60

def run_drift_breakdown():
    if not os.path.exists(DATABASE_PATH):
        print(f"Error: Database {DATABASE_PATH} not found.")
        return

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
        return

    prices = df.pivot(index='date', columns='ticker', values='adj_close')
    returns = prices.pct_change().dropna()

    print("\n" + "="*60)
    print(" PROJECT ALPHA: TICKER DRIFT BREAKDOWN (60-Day Rolling)")
    print("="*60)
    
    latest_date = returns.index[-1]
    print(f"Analysis Date: {latest_date.strftime('%Y-%m-%d')}")
    print("-" * 60)

    # Calculate individual correlations to SPY
    breakdown = {}
    for asset in DEFENSIVE_ASSETS:
        corr = returns[asset].rolling(window=ROLLING_CORRELATION_DAYS).corr(returns[MARKET_ASSET])
        latest_corr = corr.iloc[-1]
        breakdown[asset] = latest_corr
        
        # Determine drift status for this asset (using 0.10 threshold)
        status = "⚠️ DRIFT" if latest_corr > 0.10 else "✅ OK"
        print(f"{asset} Correlation to SPY:  {latest_corr:.4f}  [{status}]")

    avg_corr = sum(breakdown.values()) / len(breakdown)
    print("-" * 60)
    print(f"AVERAGE PORTFOLIO CORR:  {avg_corr:.4f}")
    print("="*60)

    # Identify the primary driver
    max_drift_asset = max(breakdown, key=breakdown.get)
    print(f"\nPRIMARY DRIFT DRIVER: {max_drift_asset}")
    print(f"This asset is moving most closely with the market, reducing its defensive utility.")

if __name__ == "__main__":
    run_drift_breakdown()
