# Project Alpha: Research & Study Log

## Phase 1: Institutional Foundation (Feb 10 - Feb 20)

### 1. Library Security Audit
- **Target:** pandas, numpy, SQLAlchemy, statsmodels, openpyxl.
- **Status:** Verified. These are standard, well-maintained libraries. 
- **Decision:** Minimize use of community-maintained wrappers (like yfinance) in favor of direct API calls to Tiingo for the "Golden Source."

### 2. Quantitative Model Study
- **Objective:** Design the math for Rolling Correlations and Monte Carlo VaR.
- **Status:** In Progress. Mapping the transition from price expression to risk conviction.

### 3. POC: Sector ETF Universe
- **Symbols:** XLB, XLC, XLE, XLF, XLI, XLK, XLP, XLRE, XLU, XLV.
- **Objective:** Verify "Two-Column" schema and "Backward Adjustment" logic on SQLite.
