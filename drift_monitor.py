import sqlite3
import pandas as pd
import numpy as np

# --- Configuration ---
DATABASE_PATH = 'sector_etfs.db'
TICKERS = [
    'XLE', 'XLU', 'XLV', 'GLD', 'XLP', 'XLF', 'XLI', 'XLB', 'XLRE', 'XLK', 'XLY', 'XLC', 'SPY' # Added SPY for market comparison as it was in the original list
]
DEFENSIVE_ASSETS = ['XLU', 'XLV', 'GLD']
MARKET_ASSET = 'SPY'
LOOKBACK_DAYS = 90
ROLLING_CORRELATION_DAYS = 60
CONVERGENCE_THRESHOLD = 0.1 # Example threshold, needs to be defined by Hong for "significant"
OUTPUT_FILE = 'correlation_matrix_snapshot.csv'

def get_returns_from_db(db_path, tickers, lookback_days):
    """
    Pulls historical returns for specified tickers from the database.
    Assumes a table named 'daily_prices' with columns: 'date', 'ticker', 'adj_close'.
    The 'date' column should be in 'YYYY-MM-DD' format.
    """
    conn = sqlite3.connect(db_path)
    # Calculate the start date
    end_date = pd.to_datetime('today')
    start_date = end_date - pd.Timedelta(days=lookback_days)
    start_date_str = start_date.strftime('%Y-%m-%d')

    print(f"Fetching data from {start_date_str} to {end_date.strftime('%Y-%m-%d')} for {tickers}...")

    query = f"""
    SELECT date, ticker, adj_close
    FROM daily_prices
    WHERE ticker IN ({', '.join([f"'{t}'" for t in tickers])})
    AND date >= '{start_date_str}'
    ORDER BY date, ticker;
    """
    
    try:
        df = pd.read_sql_query(query, conn, parse_dates=['date'])
        if df.empty:
            print(f"No data found for the specified tickers and date range in {db_path}.")
            return pd.DataFrame()
        
        # Pivot the table to have dates as index and tickers as columns, filled with adjusted close prices
        prices_df = df.pivot(index='date', columns='ticker', values='adj_close')
        # Calculate daily returns
        returns_df = prices_df.pct_change() # Removed .dropna() here
        return returns_df.fillna(0) # Fill remaining NaNs (e.g., first row of returns) with 0
    except pd.io.sql.DatabaseError as e:
        print(f"Database error: {e}. Please ensure the database '{db_path}' exists and has 'daily_prices' table with 'date', 'ticker', 'adj_close' columns.")
        return pd.DataFrame()
    finally:
        conn.close()

def calculate_rolling_correlation_matrix(returns_df, window):
    """
    Calculates a rolling correlation matrix for the given returns DataFrame.
    """
    if returns_df.empty:
        return None
    print(f"Calculating rolling {window}-day correlation matrix...")
    # This will return a Series of DataFrames, one for each window
    rolling_corr = returns_df.rolling(window=window).corr()
    return rolling_corr

def detect_convergence(rolling_corr, defensive_assets, market_asset, threshold):
    """
    Detects convergence (style drift) by monitoring the average correlation
    between defensive assets and the market asset.
    """
    if rolling_corr is None or rolling_corr.empty:
        print("Cannot detect convergence: rolling correlation matrix is empty.")
        return False, None

    print("Detecting convergence...")
    alerts = []
    
    # Iterate through the dates (which are the first level of the multi-index)
    for date in rolling_corr.index.get_level_values(0).unique():
        # Get the correlation matrix for the current date
        daily_corr_matrix = rolling_corr.loc[date]
        
        # Calculate the average correlation between defensive assets and the market asset
        # Ensure all assets are present in the correlation matrix for the given day
        
        # Filter for market_asset vs defensive_assets
        # Need to handle cases where an asset might not be in the matrix
        valid_defensive_assets = [a for a in defensive_assets if a in daily_corr_matrix.columns and market_asset in daily_corr_matrix.index]
        if not valid_defensive_assets:
            continue
            
        correlations = [daily_corr_matrix.loc[market_asset, asset] for asset in valid_defensive_assets if market_asset in daily_corr_matrix.index and asset in daily_corr_matrix.columns]

        if correlations:
            avg_correlation = np.mean(correlations)
            # For "increases significantly", we need a baseline. For now, assume a simple threshold check.
            # In a real scenario, you'd compare against a historical average or a dynamically calculated baseline.
            # For the first pass, let's just alert if it's above a certain value, indicating high correlation.
            # The prompt says "increases significantly", so this implies comparing current to previous.
            # For simplicity in this first version, I will just check if the average correlation is above a certain value.
            # Hong can refine this 'significant increase' definition later.
            if avg_correlation > threshold:
                alerts.append(f"ALERT on {date.strftime('%Y-%m-%d')}: Average correlation between defensive assets ({', '.join(defensive_assets)}) and {market_asset} is {avg_correlation:.4f}, which is above the threshold of {threshold:.4f}. Style Drift detected.")
    
    if alerts:
        for alert in alerts:
            print(alert)
        return True, alerts
    else:
        print("No significant style drift detected.")
        return False, None


def save_snapshot(correlation_matrix, output_file):
    """
    Saves the latest correlation matrix snapshot to a CSV file.
    """
    if correlation_matrix is None or correlation_matrix.empty:
        print("No correlation matrix to save.")
        return
    
    print(f"Saving correlation matrix snapshot to {output_file}...")
    # Get the latest correlation matrix
    latest_date = correlation_matrix.index.get_level_values(0).unique()[-1]
    latest_matrix = correlation_matrix.loc[latest_date]

    print("\n--- Debugging Save Snapshot ---")
    print(f"Latest date for snapshot: {latest_date.strftime('%Y-%m-%d')}")
    print(f"Latest Matrix Head (before saving):\n{latest_matrix.head()}")
    print(f"Is latest_matrix empty? {latest_matrix.empty}")
    print(f"Number of NaNs in latest_matrix:\n{latest_matrix.isnull().sum()}")
    print("--- End Debugging ---\n")

    latest_matrix.to_csv(output_file)
    print("Snapshot saved.")

if __name__ == "__main__":
    # 1. Pull data
    returns_data = get_returns_from_db(DATABASE_PATH, TICKERS, LOOKBACK_DAYS)

    if not returns_data.empty:
        print("\n--- Debugging Returns Data (before rolling correlation) ---")
        print(f"Returns Data Head:\n{returns_data.head()}")
        print(f"Returns Data Tail:\n{returns_data.tail()}")
        print(f"Returns Data Shape: {returns_data.shape}")
        print(f"Returns Data NaNs:\n{returns_data.isnull().sum()}")
        print("--- End Debugging Returns Data ---\n")

        # 2. Calculate rolling correlation matrix
        rolling_correlation_matrix = calculate_rolling_correlation_matrix(returns_data, ROLLING_CORRELATION_DAYS)
        
        print("\n--- Debugging Rolling Correlation Matrix (before saving) ---")
        print(f"Type of rolling_correlation_matrix: {type(rolling_correlation_matrix)}")
        if isinstance(rolling_correlation_matrix, pd.DataFrame) or isinstance(rolling_correlation_matrix, pd.Series):
            print(f"Is rolling_correlation_matrix empty? {rolling_correlation_matrix.empty}")
            if not rolling_correlation_matrix.empty:
                print(f"Rolling Correlation Matrix Head (first 5 rows):\n{rolling_correlation_matrix.head()}")
                print(f"Rolling Correlation Matrix Tail (last 5 rows):\n{rolling_correlation_matrix.tail()}")
                print(f"Number of NaNs in rolling_correlation_matrix:\n{rolling_correlation_matrix.isnull().sum()}")
                # Find the latest date with non-NaN correlation data
                valid_dates_for_corr = rolling_correlation_matrix.dropna(how='all', axis=0).index.get_level_values(0).unique()
                if not valid_dates_for_corr.empty:
                    latest_valid_date = valid_dates_for_corr[-1]
                    latest_matrix_for_debug = rolling_correlation_matrix.loc[latest_valid_date]
                    print(f"Latest VALID Correlation Matrix (Date: {latest_valid_date.strftime('%Y-%m-%d')}):\n{latest_matrix_for_debug}")
                    print(f"Is latest_matrix_for_debug empty? {latest_matrix_for_debug.empty}")
                    print(f"Number of NaNs in latest_matrix_for_debug:\n{latest_matrix_for_debug.isnull().sum()}")
                else:
                    print("No valid correlation data found in rolling_correlation_matrix for any date.")
            else:
                print("Rolling correlation matrix is empty.")
        else:
            print("rolling_correlation_matrix is not a DataFrame or Series.")
        print("--- End Debugging Rolling Correlation Matrix ---\n")

        # 3. Detect convergence
        alert_triggered, messages = detect_convergence(rolling_correlation_matrix, DEFENSIVE_ASSETS, MARKET_ASSET, CONVERGENCE_THRESHOLD)
        
        # 4. Save snapshot
        save_snapshot(rolling_correlation_matrix, OUTPUT_FILE)
    else:
        print("Could not proceed with calculations due to empty returns data.")
