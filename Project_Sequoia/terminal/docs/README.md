# Alpha Terminal - API Documentation

## Overview
A Bloomberg-style terminal for equity and options analysis with real-time (delayed) quotes, charts, and option chains.

## Server
- **Port:** 9090
- **Tech:** Python HTTP server + yfinance

---

## API Endpoints

### 1. Quotes
```
GET /api/quotes?tickers=SPY,QQQ
```
Returns quote data for comma-separated tickers.

**Response:**
```json
{
  "SPY": {
    "ticker": "SPY",
    "price": 634.09,
    "change": -11.0,
    "change_pct": -1.7,
    "open": 642.5,
    "high": 642.66,
    "low": 633.105,
    "volume": 92763808,
    ...
  }
}
```

---

### 2. Chart Data
```
GET /api/chart?ticker=SPY&tf=1Y
```
Returns historical price data for charting.

**Parameters:**
- `ticker` - Stock symbol (required)
- `tf` - Timeframe: 1D, 1W, 1M, 3M, YTD, 1Y, 5Y

**Response:**
```json
{
  "labels": ["2025-03-29", ...],
  "prices": [630.5, ...],
  "volumes": [1000000, ...],
  "high": [635.0, ...],
  "low": [628.0, ...],
  "open": [632.0, ...]
}
```

---

### 3. Options Chain
```
GET /api/options?ticker=SPY&expiry=2025-04-18
```
Returns option chain for given ticker and expiration.

**Response:**
```json
{
  "spot": 634.09,
  "calls": [
    {"strike": 630, "bid": 5.2, "ask": 5.4, "last": 5.3, "vol": 1000, "oi": 5000, "iv": 0.15, "delta": 0.55, "gamma": 0.02, "theta": -0.03, "vega": 0.15},
    ...
  ],
  "puts": [...]
}
```

---

### 4. Expirations
```
GET /api/expirations?ticker=SPY
```
Returns available expiration dates for a ticker.

**Response:**
```json
{
  "ticker": "SPY",
  "expirations": ["2025-04-04", "2025-04-11", ...],
  "standard": [{"date": "2025-04-18", "label": "Apr 2025 (Std)"}]
}
```

---

### 5. Ratio Analysis
```
GET /api/ratio?t1=XLE&t2=SPY&tf=1Y&sma=200
```
Returns ratio data between two tickers with technical indicators.

**Parameters:**
- `t1` - Numerator ticker
- `t2` - Denominator ticker  
- `tf` - Timeframe
- `sma` - SMA period

**Response:**
```json
{
  "labels": [...],
  "ratio": [...],
  "sma": [...],
  "rsi": [...],
  "macd": [...],
  "macd_signal": [...],
  "macd_hist": [...],
  "upper": [...],
  "lower": [...]
}
```

---

### 6. Option Screener
```
GET /api/screen?ticker=SPY
```
Scans options across multiple expirations.

**Response:**
```json
{
  "ticker": "SPY",
  "results": [
    {"strike": 630, "type": "call", "expiry": "2025-04-18", "bid": 5.2, ...},
    ...
  ]
}
```

---

## Pages

| Page | File | Description |
|------|------|-------------|
| Dashboard | dashboard.html | Quote dashboard with multi-timeframe charts |
| OMON | omon.html | Option monitor with chain + Greeks |
| Screener | screener.html | Options screener |
| Ratio | ratio.html | Ratio analysis with indicators + watchlist |

---

## Configuration

Edit `server.py` to change:
```python
DEFAULT_PORT = 9090
```

---

## Dependencies

- yfinance
- pandas
- numpy
- scipy (for Greeks)
- Chart.js (frontend)
