# Project Nine Street (NS) - Quantitative Systems Architecture

**Status:** Draft - Pre-Implementation
**Author:** NS Quantitative Architecture Team (Chuck)
**Target Horizon:** Medium-Term (30-90 Days)
**Core Philosophy:** "We do not predict the market. We model probability distributions, isolate statistical edges, and size positions to survive tail events."

---

## 1. Core Technology Stack & Open Source Libraries
To operate at an institutional level, we must rely on highly performant, heavily tested open-source libraries. We will not reinvent the wheel for standard pricing or data aggregation.

### Infrastructure & Data Tier
*   **PostgreSQL 18.3:** The single source of truth for all time-series data, options chains, and generated signals. Ensures no look-ahead bias via strict timestamping.
*   **OpenBB SDK & yfinance:** Our primary market data aggregators. OpenBB for broad macro/options coverage, and `yfinance` as a highly reliable fallback for EOD equity data.

### NS Alpha Engine (NSAE) Tier
*   **`pandas` & `numpy`:** Core vectorized data manipulation. All feature engineering must be vectorized for speed and backtesting accuracy.
*   **`scikit-learn`:** Used for cross-sectional normalization (e.g., `StandardScaler`, `MinMaxScaler`) to convert raw indicator data into our normalized `-1.0` to `+1.0` continuous conviction signals.
*   **`pytimetk` & `pandas-ta`:** `pytimetk` for advanced time-series manipulation/seasonality, and `pandas-ta` for C-optimized technical feature engineering (SMA, EMA, RSI, MACD, ATR, Bollinger Bands).
*   **`vectorbt` (and/or `zipline`):** `vectorbt` is our primary choice for blazing-fast, vectorized backtesting of the NSAE signals against historical data (evaluating millions of parameter combinations in seconds).
*   **`Plotly`:** For rendering interactive, high-performance UI components (Signal Heatmaps, P&L charts) directly in the Alpha Terminal.

### NS Option Engine (NSOE) Tier
*   **`vollib`:** The core pricing engine. Used for calculating Black-Scholes-Merton option prices, Implied Volatility (IV), and all first and second-order Greeks (Delta, Gamma, Theta, Vega, Rho).
*   **`scipy`:** For statistical modeling, normal distribution mapping (probability of profit calculations), and optimizing strike selections.

---

## 2. NS Alpha Engine (NSAE) Architecture
**Mandate:** Identify directional momentum and mean-reversion anomalies over a 30-90 day horizon, generating positive Alpha vs. SPY.

### Pipeline Architecture:
1.  **Data Ingestion Module:** Fetches daily adjusted close and volume via OpenBB. Stores in Postgres.
2.  **Feature Engineering Module (`nsae_features.py`):**
    *   Calculates Momentum Factors: 50/100/200 SMAs, MACD.
    *   Calculates Mean Reversion Factors: 14-day RSI, Bollinger Band %B.
    *   Calculates Volatility Factors: 20-day ATR (Average True Range).
    *   *Jane Street Rigor:* All features are normalized (e.g., Z-scores) so disparate assets can be compared equivalently.
3.  **Alpha Model (Signal Generator):**
    *   Combines features to output a continuous signal from `-1.0` (Strong Sell) to `+1.0` (Strong Buy), rather than binary True/False.
4.  **Risk & Portfolio Construction Module (`nsae_risk.py`):**
    *   *Volatility Targeting:* Position sizing is inversely proportional to ATR. Highly volatile assets get smaller absolute dollar allocations to maintain equal risk contribution across the portfolio.
    *   Sets dynamic stop-losses (e.g., Entry - 2x ATR).

---

## 3. NS Option Engine (NSOE) Architecture
**Mandate:** Exploit the Volatility Risk Premium (VRP) by systematically selling overpriced options (Theta/Vega decay) while strictly defining maximum drawdown via spreads.

### Pipeline Architecture:
1.  **Volatility Surface Screener (`nsoe_screener.py`):**
    *   Uses OpenBB to scan the SPY and highly liquid sector ETFs for elevated IV Rank (IVR > 50) and IV Percentile. We only sell premium when it is statistically expensive.
2.  **Pricing & Greek Engine (`nsoe_pricing.py`):**
    *   Passes OpenBB chain data through `vollib`.
    *   Calculates the exact Delta of short/long legs to estimate Probability of Profit (POP). E.g., targeting ~16 Delta for short legs (~84% statistical probability of expiring out of the money).
3.  **Strategy Selector:**
    *   Ingests the continuous signal from NSAE:
        *   NSAE Signal `> 0.5`: Deploy **Bull Put Spreads**.
        *   NSAE Signal `< -0.5`: Deploy **Bear Call Spreads**.
        *   NSAE Signal between `-0.5` and `0.5`: Deploy **Iron Condors** (Neutral).
4.  **Risk Aggregation Module:**
    *   Calculates the Portfolio Net Delta, Net Theta, and Net Vega.
    *   *Jane Street Rigor:* Hard limits on maximum spread width. Maximum portfolio loss across all open spreads must not exceed 2% of total AUM.

---

## 4. Institutional Backtesting Philosophy (NSAE & NSOE)
To ensure strategy robustness and survival through tail events, backtesting must adhere to strict institutional standards:
1.  **Zero Look-Ahead Bias:** All features are strictly shifted by `T+1`. Signals generated at today's close are executed at tomorrow's open price.
2.  **Slippage & Friction Inclusion:** All backtests automatically deduct 5 to 10 basis points (bps) for equity slippage, and assume options are filled at the mid-price minus $0.05.
3.  **Walk-Forward Optimization:** No curve-fitting. Strategies are trained on a 2-year rolling window and tested on an out-of-sample 6-month window to ensure adaptability to changing volatility regimes.

---

## 5. The Nine Street Daily Workflow (Exploration to Execution)
Since execution remains manual, the system operates on a rigid daily schedule to synthesize machine-math with human approval.

*   **Phase 1: Exploration (4:15 PM EST)**
    *   Cron jobs trigger OpenBB to fetch EOD data.
    *   `nsae_features.py` calculates the continuous `-1.0` to `+1.0` signals.
    *   `nsoe_screener.py` identifies high IVR options setups.
*   **Phase 2: Agent Consensus (4:30 PM EST)**
    *   *Equity/FICC Agents:* Review the heatmap and establish directional bias.
    *   *Quant Agent:* Identifies specific strikes/spreads (e.g., 16 Delta) and calculates Probability of Profit.
    *   *Risk Agent:* Vetoes any trade where Max Loss exceeds 1.0% of portfolio AUM.
*   **Phase 3: Approval & Execution (4:45 PM EST)**
    *   *Strategist Agent:* Formats the approved trades into a JSON/Markdown ticket and posts to Discord `#sequoia-trade-ideas`.
    *   *Human (Hong):* Reviews the logic and manually executes the trade for the next morning's open.
*   **Phase 4: Monitoring & Closing (Daily)**
    *   *NSAE (Equities):* Hold until the signal crosses `0.0` or hits the dynamic trailing stop (Entry - 2x ATR).
    *   *NSOE (Options):* Mechanical exit at 50% of Max Profit (harvesting Theta), or exactly at 21 DTE to eliminate Gamma/tail risk.

---

## 6. Phase 2 Roadmap: Regime-Gated Architecture (HMM/GMM Integration)
**Target:** Post-Baseline Implementation
To upgrade Nine Street to a true institutional-grade quantitative architecture, we will integrate Hidden Markov Models (HMM) and Gaussian Mixture Models (GMM) directly into the pipeline.

*   **Context-Aware TA (Solving the Indicator Trap):** Use the HMM regime state to dynamically gate technical signals. In a "Trending" regime, ignore overbought RSI and buy moving average pullbacks. In a "Chop" regime, ignore MA crossovers and fade extremes using RSI.
*   **Cross-Asset Regime Coherence:** Fit HMMs simultaneously across Equities (SPY), Bonds (TLT), and Commodities (GLD) to detect macro divergences (e.g., SPY breaking out while TLT/GLD signal risk-off).
*   **Options Engine Synergy:** If the HMM detects a high probability of remaining in a "Compression" regime, deploy Iron Condors to harvest theta. If volatility compression TA signals an imminent regime transition (breakout), automatically ban Iron Condors and shift to directional spreads.