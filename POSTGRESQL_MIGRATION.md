# PostgreSQL Migration Plan (Project Alpha)

## 1. Overview
Transitioning the Project Alpha database from SQLite (`sector_etfs.db`) to PostgreSQL to support:
-   **Concurrency:** Multiple clients (Dashboard, Bot, Backtester) accessing data simultaneously.
-   **Performance:** Faster aggregations for large historical datasets.
-   **Reliability:** Robust ACID compliance for transaction integrity.

## 2. Current Schema (SQLite)

### Table: `daily_prices`
| Column | Type | Constraints |
| :--- | :--- | :--- |
| `ticker` | TEXT | NOT NULL |
| `date` | TEXT | NOT NULL |
| `raw_close` | REAL | |
| `adj_close` | REAL | |
| `volume` | INTEGER | |
| **PK** | `(ticker, date)` | |

### Table: `portfolio_v1_performance`
| Column | Type | Constraints |
| :--- | :--- | :--- |
| `date` | TEXT | PRIMARY KEY |
| `total_portfolio_value` | REAL | |
| `daily_return_vs_spy` | REAL | |
| `cumulative_alpha` | REAL | |

## 3. Target Schema (PostgreSQL 16)

### Optimization: Partitioning
For `daily_prices`, we will use **Range Partitioning** by `date`.
*   **Benefit:** Queries filtering by date (e.g., "last 30 days") will only scan the relevant partition, significantly improving performance.

### SQL Definition
```sql
-- 1. Create Parent Table
CREATE TABLE daily_prices (
    ticker VARCHAR(10) NOT NULL,
    date DATE NOT NULL,
    raw_close NUMERIC(10, 4),
    adj_close NUMERIC(10, 4),
    volume BIGINT,
    CONSTRAINT pk_daily_prices PRIMARY KEY (ticker, date)
) PARTITION BY RANGE (date);

-- 2. Create Partitions (e.g., Yearly)
CREATE TABLE daily_prices_2024 PARTITION OF daily_prices
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');

CREATE TABLE daily_prices_2025 PARTITION OF daily_prices
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');

CREATE TABLE daily_prices_2026 PARTITION OF daily_prices
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');

-- 3. Performance Table (Standard)
CREATE TABLE portfolio_v1_performance (
    date DATE PRIMARY KEY,
    total_portfolio_value NUMERIC(12, 2),
    daily_return_vs_spy NUMERIC(8, 6),
    cumulative_alpha NUMERIC(8, 6)
);

-- 4. Indexing Strategy
-- Composite index for time-series queries
CREATE INDEX idx_daily_prices_ticker_date ON daily_prices (ticker, date DESC);
-- Index for portfolio date lookups
CREATE INDEX idx_portfolio_date ON portfolio_v1_performance (date DESC);
```

## 4. Migration Steps

1.  **Provision PostgreSQL 16:** Install on host or provision managed instance (e.g., Supabase, Neon, or local Docker).
2.  **Data Dump (SQLite):**
    ```bash
    sqlite3 sector_etfs.db .dump > backup.sql
    ```
3.  **Data Transform & Load:**
    *   Use `pandas` (`to_sql`) or `pgloader` to move data.
    *   Convert `YYYY-MM-DD` strings to PostgreSQL `DATE` type.
4.  **Verification:**
    *   Run `risk_manager.py` checks against new DB.
    *   Verify `daily_returns` calculations.

## 5. Next Steps
-   [ ] Confirm Host for PostgreSQL (Local vs. Cloud).
-   [ ] Draft connection string for `config.py`.
-   [ ] Test Partitioning performance.
