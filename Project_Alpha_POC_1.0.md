# Project Alpha POC: Consolidated Design Document (Version 1.0)

---

## I. Project Overview & Objectives

**Project Name:** Project Alpha
**Objective:** To build a foundational, end-to-end systematic strategy framework, starting with a SQLite database for the Proof of Concept, and demonstrating the integration of QuantScience's "Multi-Asset Systematic Strategy Design" principles. This will evolve into a robust system utilizing PostgreSQL for production.

### Core Principles (Derived from QuantScience Thread)

1.  **Hierarchy of Returns:** For cross-asset portfolios, Beta (Market Exposure) is the base, but Alternative Beta (Factors) and Alpha (Selection) are the goals.
2.  **Risk Management as the Core:** Risk Budgeting should drive the allocation, not just expected return.
3.  **Need for "Point-in-Time" Data:** Avoiding "look-ahead bias" in backtesting by using only data known at the time.
4.  **Continuous Feedback Loops:** Systematic strategies must be monitored for "Style Drift" or changes in correlation.

---

## II. Core Strategy: Stress-Adjusted Baseline

*(Based on `STRATEGY_BASELINE_V1.md` - February 11th, 2026)*

### 1. Overview
A cross-asset defensive strategy designed to outperform the S&P 500 (SPY) by minimizing tail risk in volatile macro conditions.

### 2. Strategic Bias
*   **Overweight:** Energy (XLE), Utilities (XLU), Healthcare (XLV), Gold (GLD).
*   **Underweight:** Technology (XLK), Consumer Discretionary (XLY).
*   **Philosophy:** Alpha through variance reduction. Protecting the downside allows for superior compound growth.

### 3. Backtest Benchmarks (YTD Feb 11, 2026)
*   Portfolio Return: +3.12%
*   SPY Return: +2.45%
*   Alpha: +0.67% (67 bps)
*   VaR (95%): -1.09%

### 4. Current Geopolitical Context
Specifically optimized for "Energy Supply Shock" scenarios. The portfolio is currently positioned to benefit from volatility in crude oil while hedging against a broader market sell-off in growth sectors.

---

## III. Data Ingestion & Integrity Engine Design

*(Based on `Project_Alpha_Ingestion_Design.md`)*

### 1. System Philosophy
Prioritizing **Data Integrity** over speed. Every data point must be validated and adjusted for corporate actions to ensure institutional-grade analysis.

### 2. The Data Ingestion Engine
*   **Primary Source:** Tiingo API
*   **Logic:** Modular "Fetcher" classes with a centralized "Adjustment Processor."
*   **Storage Strategy:** Two-Column (Raw vs. Adjusted) to preserve both historical auditability and analytical accuracy.

### 3. Corporate Action Coverage
*   **Stock Splits:** Backward adjustment of 5-year history.
*   **Cash Dividends:** Total return adjustment via historical factors.
*   **Spin-offs:** Value-reduction adjustment to parent cost-basis.
*   **Ticker Mapping:** Continuity management for symbol changes.

### 4. Maintenance & Operations
*   **Validation Layer:** Gap and outlier detection before database commit.
*   **Automated Sliding Window:** Integrated into the daily ingestion cycle.
*   **Failure Handling:** Automated Discord alerts for API or database errors.

---

## IV. Database Architecture

### A. Current POC Database (SQLite)

*   **Database File:** `sector_etfs.db`
*   **Existing "Shadow Tracking" Logic:**
    *   A new table `portfolio_v1_performance` tracks a specific $20,000 portfolio (12 assets + cash) daily.
    *   **Starting Point:** Portfolio "purchased" with calculated share counts (e.g., 66 XLE, 4 GLD) at today's closing prices.
    *   **Daily Ingestion:** Script automatically pulls latest close prices for 12 tickers, calculates Total Portfolio Value (including cash buffer), Daily Return vs. SPY, and logs Cumulative Alpha.
    *   **Audit Trail:** Creates a permanent record for performance monitoring and future visualization.

### B. Target Production Database (PostgreSQL 16)

*(Based on `Project_Alpha_Architecture.md`)*

*   **Overview:** An institutional-grade time-series database designed for cross-asset investment and risk management.
*   **System Flow:**
    ```mermaid
    graph TD
        A[Market Data: Yahoo/Tiingo] -->|Python/API| B(Ingestion & Validation)
        B -->|Cleaned Data| C{PostgreSQL 16}
        C -->|Asset Data| D[Sliding Window: 5yr Limit]
        C -->|Ratio Data| E[Everlasting Storage]
        D --> F[Daily Report & Analysis]
        E --> F
    ```

*   **Database Schema:**
    *   **Asset Tables (Stocks, FX, Commodities, Bonds):**
        *   `ticker` (PK, Index)
        *   `date` (PK, Index)
        *   `adj_close` (Numeric)
        *   `volume` (BigInt, On-Demand)
    *   **Equity Options Table:**
        *   `ticker` / `strike` / `type` / `expiry` (Indices)
        *   `close_price`
        *   `auto_cleanup_flag` (True after expiry)
    *   **Intermarket Ratio Table:**
        *   `num_ticker` / `den_ticker`
        *   `ratio_value`
        *   `date` (No cleanup logic)

*   **Maintenance Logic:**
    *   **Weekly Cron:** `DELETE FROM asset_tables WHERE date < NOW() - INTERVAL '5 years'`
    *   **Daily Cron:** `DELETE FROM options_table WHERE expiry < NOW()`
*   **Planned Installation:** Hong will install PostgreSQL in person on February 20th or 21st. The transition to PostgreSQL and more asset classes will occur after the POC is approved and PostgreSQL is ready.

---

## V. High-Level POC Components (Leveraging SQLite)

These components will be developed and integrated, utilizing the existing `sector_etfs.db` SQLite database and the "Shadow Tracking" logic for the Proof of Concept phase.

1.  **Data Ingestion & Storage (`data_handler.py` / `sector_etfs.db`):**
    *   **Purpose:** Builds upon existing "Shadow Tracking" to ensure robust, point-in-time data handling for prices and corporate events within SQLite.
    *   **Integration:** Will interface with the Tiingo API for data and store it in `sector_etfs.db`, adhering to the Two-Column Strategy.

2.  **Strategy Engine (`strategy_engine.py`):**
    *   **Purpose:** Implements the core logic for generating signals and allocations based on the "Stress-Adjusted Baseline" strategy.
    *   **Functionality:** Calculates Beta, integrates the "Geopolitical Factor" (e.g., overweighting Energy), and outputs asset allocations for the $20,000 portfolio.

3.  **Risk Management Module (`risk_manager.py`):**
    *   **Purpose:** Ensures proposed allocations adhere to predefined risk budgets consistent with the "Stress-Adjusted Baseline."
    *   **Details:** Calculates VaR and Conditional VaR for the portfolio and adjusts allocations to stay within the simulated "risk budget."

4.  **Performance & Feedback Loop:**
    *   **`performance_tracker.py`:** Monitors simulated daily portfolio value, calculates returns (building on existing "Shadow Tracking"), and compares against the SPY benchmark, logging Cumulative Alpha.
    *   **`drift_monitor.py`:** Monitors correlations between the 11 sector ETFs. Detects and logs significant shifts (beyond defined thresholds) over rolling windows, signaling potential "style drift."

5.  **Backtesting Framework (`backtester.py`):**
    *   **Purpose:** Evaluates the strategy's historical performance using `sector_etfs.db`, strictly adhering to point-in-time data principles to avoid look-ahead bias.
    *   **Orchestration:** Coordinates `Data Handler`, `Strategy Engine`, and `Risk Manager` over historical data.

---

## VI. Development Approach & Future Roadmap

*   **Current Phase:** Focus on developing the POC components leveraging the existing SQLite (`sector_etfs.db`) foundation.
*   **Next Steps for POC Implementation:**
    *   Review existing `sector_etfs.db` and ingestion scripts.
    *   Develop `strategy_engine.py` to implement the "Stress-Adjusted Baseline."
    *   Develop `risk_manager.py` for VaR/CVaR calculations and allocation adjustments.
    *   Develop `performance_tracker.py` and `drift_monitor.py`.
    *   Build `backtester.py` to integrate and test the full flow.
*   **Future Roadmap (Post-POC & PostgreSQL Installation):**
    *   Transition to **GitHub** for source control.
    *   Utilize **Jira** for tracking progress, issues, and requests.
    *   Adopt standard software development practices to scale Project Alpha.
    *   Migrate data and logic to **PostgreSQL 16** for production-grade performance and scalability, incorporating more asset classes.

---

**Next Immediate Step:** I will begin by reviewing the existing `sector_etfs.db` and any associated ingestion scripts to understand the current "Shadow Tracking" implementation.