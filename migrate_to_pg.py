import sqlite3
import psycopg2
from psycopg2 import sql
import os

# --- Configuration ---
SQLITE_PATH = '/Users/chuck/.openclaw/workspace/sector_etfs.db'
POSTGRES_CONN = "dbname=project_alpha user=chuck host=localhost"

def migrate():
    # 1. Connect to both databases
    sl_conn = sqlite3.connect(SQLITE_PATH)
    sl_cursor = sl_conn.cursor()
    
    pg_conn = psycopg2.connect(POSTGRES_CONN)
    pg_cursor = pg_conn.cursor()

    print("--- Starting Migration ---")

    # 2. Create Target Schema (Simplified for initial migration)
    # Using the refined schema from POSTGRESQL_MIGRATION.md
    pg_cursor.execute("""
    CREATE TABLE IF NOT EXISTS daily_prices (
        ticker VARCHAR(10) NOT NULL,
        date DATE NOT NULL,
        raw_close NUMERIC(10, 4),
        adj_close NUMERIC(10, 4),
        volume BIGINT,
        PRIMARY KEY (ticker, date)
    );
    """)
    
    pg_cursor.execute("""
    CREATE TABLE IF NOT EXISTS portfolio_v1_performance (
        date DATE PRIMARY KEY,
        total_portfolio_value NUMERIC(12, 2),
        daily_return_vs_spy NUMERIC(8, 6),
        cumulative_alpha NUMERIC(8, 6)
    );
    """)

    # 3. Migrate daily_prices
    print("Migrating daily_prices...")
    sl_cursor.execute("SELECT ticker, date, raw_close, adj_close, volume FROM daily_prices")
    rows = sl_cursor.fetchall()
    for row in rows:
        pg_cursor.execute(
            "INSERT INTO daily_prices (ticker, date, raw_close, adj_close, volume) VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            row
        )

    # 4. Migrate performance
    print("Migrating portfolio_v1_performance...")
    sl_cursor.execute("SELECT date, total_portfolio_value, daily_return_vs_spy, cumulative_alpha FROM portfolio_v1_performance")
    rows = sl_cursor.fetchall()
    for row in rows:
        pg_cursor.execute(
            "INSERT INTO portfolio_v1_performance (date, total_portfolio_value, daily_return_vs_spy, cumulative_alpha) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
            row
        )

    pg_conn.commit()
    sl_conn.close()
    pg_conn.close()
    print("--- Migration Successful ---")

if __name__ == "__main__":
    migrate()
