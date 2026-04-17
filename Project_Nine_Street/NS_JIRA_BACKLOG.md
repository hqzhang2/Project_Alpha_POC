# Project Nine Street - Jira Backlog (Phase 1-4)

This document tracks the Epics and Jira Tickets for the 4-Layer Quantitative Architecture overhaul.

## EPIC 1: Layer 1 - Institutional Feature Engineering
**Description:** Overhaul the NSAE Feature Engineering module to compress raw ETF price/volume data into regime-relevant features. We are moving away from raw returns to a rich feature vector.
**Goal:** Output a clean, scaled feature matrix `[ADX, RSI, BB_width, vol_ratio, OBV_slope]` without lookahead bias.

*   **[NS-101] Refactor `nsae_features.py` for Target Feature Vector**
    *   **Type:** Task
    *   **Description:** Implement `pandas_ta` logic to calculate ADX, RSI, Bollinger Band Width, Volatility Ratio, and OBV Slope. Strip out any generic features that aren't strictly required for the HMM.
    *   **Acceptance Criteria:** `fetch_data()` outputs a DataFrame containing exactly these 5 engineered columns alongside price data.
*   **[NS-102] Implement `TimeSeriesSplit` Normalization**
    *   **Type:** Task
    *   **Description:** Standard `MinMaxScaler` across the whole dataset introduces lookahead bias. Implement a rolling or expanding window scaler to normalize features cross-sectionally without leaking future data.
    *   **Acceptance Criteria:** Features are scaled strictly using trailing data (e.g., rolling 252-day window).

## EPIC 2: Layer 2 - GMM/HMM State Estimation
**Description:** Upgrade the quantitative models to use Gaussian Mixture Models (GMM) combined with Hidden Markov Models (HMM). The model must ingest the feature vector from Layer 1 to detect latent market regimes.

*   **[NS-201] Upgrade to `GMMHMM` in `ns_quant_models.py`**
    *   **Type:** Task
    *   **Description:** Replace the standard `GaussianHMM` with `hmmlearn`'s `GMMHMM`. Configure it to ingest the multi-dimensional feature vector from Layer 1 instead of raw returns.
    *   **Acceptance Criteria:** `detect_market_regime()` successfully fits a `GMMHMM` model on the 5-factor feature matrix and outputs latent states.
*   **[NS-202] Integrate `ruptures` for Change-Point Detection**
    *   **Type:** Task
    *   **Description:** Add structural break detection using the `ruptures` library to cross-validate HMM state transitions. This acts as a sanity check against the Markov transition matrix.
    *   **Acceptance Criteria:** A new method `detect_structural_breaks()` is implemented and returns indices of regime shifts.
*   **[NS-203] State Mapping and Diagnostics**
    *   **Type:** Task
    *   **Description:** Latent states (0, 1, 2) mean nothing to a human trader. Write logic to map these states to human-readable regimes (e.g., "Trending Bull", "Choppy", "High Vol Bear") by analyzing the emission means/variances of the states.
    *   **Acceptance Criteria:** Output states are mapped to descriptive string labels.

## EPIC 3: Layer 3 - Regime-Gated Signal Generation
**Description:** Build the logic that connects technical signals to market regimes. Signals must be dynamically suppressed or activated based on the current HMM state.

*   **[NS-301] Construct the Signal Gating Matrix**
    *   **Type:** Task
    *   **Description:** Define the strict mathematical rules for signal gating (e.g., RSI active only in mean-reverting regimes, MACD/Moving Average crossovers active only in trending regimes).
    *   **Acceptance Criteria:** A configuration dictionary or matrix mapping indicators to permitted HMM states.
*   **[NS-302] Implement Vectorized Gating in `ns_backtester.py`**
    *   **Type:** Task
    *   **Description:** Apply the gating matrix to historical data using vectorized `pandas`/`numpy` operations.
    *   **Acceptance Criteria:** The backtester outputs final, regime-adjusted trading signals (1, 0, -1) and calculates performance metrics.

## EPIC 4: Layer 4 - QA Dashboard Visualization & Integration
**Description:** Pipe the generated regimes and gated signals to the QA Dashboard (Port 9199) so the human trader can visually verify model accuracy against historical charts.

*   **[NS-401] Update `server.py` API endpoints**
    *   **Type:** Task
    *   **Description:** Modify the `/api/chart` endpoint to serve the HMM regime overlays, state transition probabilities, and gated signals alongside the standard OHLCV data.
    *   **Acceptance Criteria:** JSON payload includes `regime_labels`, `regime_probs`, and `gated_signals`.
*   **[NS-402] UI Updates for Heatmap/Regimes in `index.html`**
    *   **Type:** Task
    *   **Description:** Update the frontend charting library to plot background color bands (e.g., red for high vol bear, green for low vol bull, gray for chop) or bottom-pane indicators representing the detected market regimes.
    *   **Acceptance Criteria:** The UI displays a clear visual overlay of the HMM regimes over the price chart.

*   **[NS-403] UI: Strategy & Parameter Control Panel**
    *   **Type:** Task
    *   **Description:** Build a control interface in `index.html` (e.g., a sidebar or modal) to allow the trader to dynamically adjust model parameters without touching code.
    *   **Acceptance Criteria:** UI includes inputs for Strategy Selection (e.g., "Regime-Gated RSI", "Regime-Gated MACD"), HMM States (n_components), and Indicator Parameters (e.g., RSI Length, BB StdDev).
*   **[NS-404] UI/API: Interactive Backtest Execution & Reporting**
    *   **Type:** Task
    *   **Description:** Wire the frontend to the `ns_backtester.py` engine. Allow the user to run backtests on the fly with the selected parameters and visualize the performance.
    *   **Acceptance Criteria:** Add an `/api/backtest` endpoint. UI displays the resulting Equity Curve, Sharpe Ratio, Sortino Ratio, Max Drawdown, and Win Rate.
