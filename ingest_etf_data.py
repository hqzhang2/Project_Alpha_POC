
import yfinance as yf
import sqlite3
import pandas as pd
from datetime import datetime, timedelta

DB_NAME = "sector_etfs.db"

def fetch_and_ingest_historical_data():
    tickers = [
        "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY", "GLD", "SPY"
    ]
    end_date = datetime.today()
    start_date = end_date - timedelta(days=5*365) # 5 years of data

    print(f"Fetching data from {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")

    try:
        data = yf.download(tickers, start=start_date, end=end_date)
        if data.empty:
            print("No data downloaded. Check tickers or date range.")
            return

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        for ticker in tickers:
            # Check if 'Adj Close' column exists for the ticker
            if ('Adj Close', ticker) in data.columns:
                ticker_adj_close_data = data.loc[:, ('Adj Close', ticker)].dropna()
            elif ('Close', ticker) in data.columns: # Fallback to 'Close' if 'Adj Close' is not found
                print(f"Warning: ('Adj Close', '{ticker}') not found. Using ('Close', '{ticker}') for adjusted close.")
                ticker_adj_close_data = data.loc[:, ('Close', ticker)].dropna()
            else:
                print(f"Error: Neither 'Adj Close' nor 'Close' found for {ticker}. Skipping.")
                continue
            
            # Ensure 'Close' and 'Volume' columns exist
            if ('Close', ticker) in data.columns:
                raw_close_data = data.loc[:, ('Close', ticker)].dropna()
            else:
                print(f"Error: ('Close', '{ticker}') not found. Skipping.")
                continue

            if ('Volume', ticker) in data.columns:
                volume_data = data.loc[:, ('Volume', ticker)].dropna()
            else:
                print(f"Error: ('Volume', '{ticker}') not found. Skipping.")
                continue

            # Align indices to ensure dates match for adj_close, raw_close, and volume
            aligned_data = pd.DataFrame({
                'adj_close': ticker_adj_close_data,
                'raw_close': raw_close_data,
                'volume': volume_data
            }).dropna()

            if aligned_data.empty:
                print(f"No aligned data for {ticker}. Skipping.")
                continue

            for date, row in aligned_data.iterrows():
                cursor.execute("""
                    INSERT OR REPLACE INTO daily_prices (ticker, date, raw_close, adj_close, volume)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    ticker,
                    date.strftime('%Y-%m-%d'),
                    row['raw_close'],
                    row['adj_close'],
                    row['volume']
                ))
        conn.commit()
        conn.close()
        print(f"Successfully ingested historical data for {len(tickers)} tickers into {DB_NAME}")

    except Exception as e:
        print(f"An error occurred during data ingestion: {e}")

if __name__ == "__main__":
    fetch_and_ingest_historical_data()
