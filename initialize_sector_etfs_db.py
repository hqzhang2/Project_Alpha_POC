
import sqlite3
import os

DB_NAME = "sector_etfs.db"

def initialize_db():
    if os.path.exists(DB_NAME):
        print(f"Existing database {DB_NAME} found. Deleting and recreating.")
        os.remove(DB_NAME)

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Create daily_prices table (Two-Column Strategy)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_prices (
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            raw_close REAL,
            adj_close REAL,
            volume INTEGER,
            PRIMARY KEY (ticker, date)
        )
    """)

    # Create portfolio_v1_performance table (for Shadow Tracking)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_v1_performance (
            date TEXT PRIMARY KEY,
            total_portfolio_value REAL,
            daily_return_vs_spy REAL,
            cumulative_alpha REAL
        )
    """)

    conn.commit()
    conn.close()
    print(f"Database {DB_NAME} initialized successfully with daily_prices and portfolio_v1_performance tables.")

if __name__ == "__main__":
    initialize_db()
