# Sprint 1 - Simplified Bloomberg Terminal

**Target:** April 1, 2026  
**Focus:** Core Dashboard

---

## JIRAs

### KAN-19: Yahoo Finance Quote Integration
- [x] Set up Yahoo Finance API (yfinance Python lib)
- [x] Fetch real-time/delayed quotes for watchlist tickers
- [x] Handle API rate limits and caching
- [x] Output: Quote data JSON for dashboard

**Test result:**
```
SPY: $634.09 (-1.7%)
QQQ: $562.58 (-1.95%)
TLT: $85.64 (-0.5%)
```

### KAN-20: Basic Terminal Dashboard UI
- [x] Design layout (header, sidebar, main panel)
- [x] Display ticker, price, change, % change
- [x] Add refresh button / auto-refresh (30s)
- [x] Responsive design (desktop/mobile)

### KAN-21: Watchlist Management
- [x] Add/remove tickers from watchlist
- [x] Persist watchlist to file (JSON)
- [x] Default watchlist: SPY, QQQ, IWM, TLT, GLD

### KAN-22: Charting (Basic)
- [x] 1D, 1W, 1M, 3M, 1Y timeframes
- [x] Price chart with candlesticks
- [x] Volume bars below
- [x] Use lightweight charting lib (Chart.js or similar)

---

## Done ✅
- KAN-19: Yahoo Finance Quote Integration
- KAN-20: Basic Terminal Dashboard UI
- KAN-21: Watchlist Management
- KAN-22: Charting (Basic)