# Project Alpha: Data Ingestion & Integrity Engine

## 1. System Philosophy
Prioritizing **Data Integrity** over speed. Every data point must be validated and adjusted for corporate actions to ensure institutional-grade analysis.

## 2. The Data Ingestion Engine
- **Primary Source:** Tiingo API
- **Logic:** Modular "Fetcher" classes with a centralized "Adjustment Processor."
- **Storage Strategy:** Two-Column (Raw vs. Adjusted) to preserve both historical auditability and analytical accuracy.

## 3. Corporate Action Coverage
- **Stock Splits:** Backward adjustment of 5-year history.
- **Cash Dividends:** Total return adjustment via historical factors.
- **Spin-offs:** Value-reduction adjustment to parent cost-basis.
- **Ticker Mapping:** Continuity management for symbol changes.

## 4. Maintenance & Operations
- **Validation Layer:** Gap and outlier detection before database commit.
- **Automated Sliding Window:** Integrated into the daily ingestion cycle.
- **Failure Handling:** Automated Discord alerts for API or database errors.
