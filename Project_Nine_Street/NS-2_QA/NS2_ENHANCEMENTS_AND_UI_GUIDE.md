# NS-2 MAG7 HMM Regime Strategy — 7 Enhancements & UI Guide

> **Project**: Project Alpha POC / Nine Street  
> **Service**: NS-2 QA (port 9229) | NS-2 PROD (port 9228)  
> **Branch**: `feature/v1.8`  
> **Last Updated**: July 2026

---

## Part 1: The 7 Enhancements to the Original MAG7 Model

The original MAG7 model was a basic HMM-based regime detector with simple signal logic. The current `qa_server.py` implements **7 specific improvements** (documented in the file's module docstring and throughout the code as `Improvement #N` comments).

---

### **Improvement #1: 3-State HMM + 8-Feature Expanded Observation Vector**

**What changed**: Reduced from 4 HMM states → **3 stable states**; expanded from ~4 features → **8 features**.

| State | Label | Description | Color |
|-------|-------|-------------|-------|
| 0 | **TRENDING** | Strong directional move with ADX confirmation | `#76e4c4` (green) |
| 1 | **MEAN_REV** | Range-bound, oversold/overbought bounces | `#7ec8e3` (blue) |
| 2 | **CRISIS** | High volatility, capital preservation mode | `#ff6b6b` (red) |

**Why 3 states?** 4-state HMM labels were **unstable across random seeds** (see session `20260722_112341_3d1ac7` — state mapping flipped wildly with seeds 42/123/456). 3 states are more robust with ~125 bars of data.

**8 Features (FEATURE_COLS)**:
```python
FEATURE_COLS = [
    "log_return",       # Daily log return
    "rolling_vol",      # 10-day rolling volatility of returns
    "vol_ratio",        # Current vol / 20-day median vol
    "bb_position",      # Position within Bollinger Bands (-1 to +1)
    "adx",              # Average Directional Index (trend strength)
    "ma_distance",      # Distance from 50-day SMA (normalized)
    "atr_ratio",        # ATR / Close (normalized volatility)
    "volume_z",         # Volume z-score (20-day)
]
```

**Original**: Only `log_return`, `rolling_vol`, `atr_ratio` (3 features).

---

### **Improvement #2: Confidence-Weighted Position Sizing**

**File**: `size_by_confidence()` (lines 383–404) + integrated into `generate_signals_v2()`

**Logic**:
```python
base_sizes = {
    0: 1.0,      # TRENDING  → 100% position
    1: 0.60,     # MEAN_REV  → 60% position
    2: 0.10,     # CRISIS    → 10% position (capital preservation)
}

# Macro overlay
if macro_filter == -1 (RISK_OFF):  base *= 0.5
elif macro_filter == 1 (RISK_ON):  base = min(1.0, base * 1.2)
```

**Result**: Position size adapts to both regime confidence AND macro environment. CRISIS regime only takes tiny shorts (CCI < -250).

---

### **Improvement #3: Multi-Factor Signal Confirmation**

**File**: `generate_signals_v2()` (lines 392–446)

Each regime has **distinct entry/exit logic** — no more one-size-fits-all:

| Regime | Entry Condition | Exit Condition | Position Size |
|--------|-----------------|----------------|---------------|
| **TRENDING (0)** | CCI crosses **above +100** | CCI drops **below 0** | 100% |
| **MEAN_REV (1)** | RSI < **30** (oversold) | RSI > **70** (overbought) or RSI 45–55 (mean) | 60% |
| **CRISIS (2)** | CCI < **-250** (extreme panic only) | Regime change | 10% (short only) |

**Circuit Breakers** (applied to all regimes):
- **Vol expansion**: `vol_ratio > 3.0` → force FLAT (signal = 0)
- **Crisis regime entry**: If regime=2 AND previous signal=1 → force EXIT (signal = 0)

---

### **Improvement #4: VIX Macro Overlay (Risk-On / Risk-Off Filter)**

**File**: `get_macro_filter()` (lines 355–385)

| VIX Level | SPY > SMA50 | Macro Filter | Effect |
|-----------|-------------|--------------|--------|
| VIX < 15 | Yes | **RISK_ON (1)** | Boost sizes +20% |
| VIX > 25 | Any | **RISK_OFF (-1)** | Halve all sizes |
| Else | Any | **NEUTRAL (0)** | Base sizes |

**Cached** for 5 minutes (`CACHE_TTL = 300`). Used by `generate_signals_v2()` and `run_all()`.

---

### **Improvement #5: Adaptive Persistence Filter**

**File**: `apply_adaptive_persistence()` (lines 292–312)

**Problem**: HMM can flip-flop between states on noisy bars.

**Solution**: Require **N consecutive bars** of new regime before accepting transition:

| ATR Ratio | Market Condition | Persistence Required |
|-----------|------------------|---------------------|
| > 0.03 | High volatility / Crisis | **2 bars** (fast) |
| < 0.01 | Low volatility / Trend | **5 bars** (slow) |
| Else | Normal | **3 bars** (default) |

---

### **Improvement #6: ATR Trailing Stops + Drawdown Circuit Breaker**

**File**: `apply_stops()` (lines 449–471)

| Mechanism | Parameters | Trigger |
|-----------|------------|---------|
| **ATR Trailing Stop** | 3 × ATR from entry price | Close < entry - 3×ATR → force EXIT |
| **Drawdown Circuit Breaker** | `MAX_DRAWDOWN = -15%` | Equity drawdown > 15% → force EXIT all longs |

Applied **after** backtest equity curve is computed (post-signal).

---

### **Improvement #7: HMM Ensemble (5 Models, Majority Vote + Agreement Score)**

**File**: `fit_hmm_ensemble()` (lines 247–295)

| Parameter | Value |
|-----------|-------|
| **Ensemble size** | 5 models (`HMM_ENSEMBLE_N = 5`) |
| **Seeds** | 42, 43, 44, 45, 46 |
| **Aggregation** | Majority vote per bar (`scipy.stats.mode`) |
| **Agreement score** | Fraction of models agreeing with majority (0.0–1.0) |
| **Reference model** | Seed 42 (used for `predict_proba` if needed) |

**Why**: Single HMM fit is seed-dependent. Ensemble smooths label instability.

---

## Summary: Original vs Enhanced

| Aspect | Original MAG7 Model | Enhanced NS-2 (7 Improvements) |
|--------|---------------------|--------------------------------|
| HMM States | 4 (unstable labels) | **3 (TRENDING, MEAN_REV, CRISIS)** |
| Features | 3 basic | **8 expanded** |
| Position Sizing | Fixed / binary | **Confidence + macro weighted** |
| Signal Logic | One rule for all | **Regime-specific multi-factor** |
| Macro Filter | None | **VIX + SPY trend overlay** |
| Regime Persistence | None | **Adaptive (2–5 bars by vol)** |
| Risk Management | None | **ATR stops + DD circuit breaker** |
| HMM Stability | Single fit | **5-model ensemble + agreement** |
| Python 3.9 Compat | Broken (`kurtosis()`) | **Fixed (scipy.stats)** |

---

## Part 2: NS-2 Dashboard UI Guide

> **URL**: `http://localhost:9229/` (QA)  
> **Dashboard File**: `Project_Nine_Street/NS-2_QA/ns2_dashboard.html`

---

### 1. Header & Macro Badge

| Element | Description |
|---------|-------------|
| **Title** | `mag7 hmm` |
| **Macro Badge** | Shows current VIX regime: 🟢 RISK_ON · 🟡 NEUTRAL · 🔴 RISK_OFF (with VIX thresholds) |
| **QA Badge** | `QA :9229` (green badge = QA environment) |

---

### 2. Ticker Pills (Horizontal Scroll)

| Ticker | Color | Action |
|--------|-------|--------|
| **AAPL** | `#a8d8a8` | Click to load |
| **MSFT** | `#7ec8e3` | Click to load |
| **NVDA** | `#76e4c4` | Click to load |
| **GOOGL** | `#f7c59f` | Click to load |
| **AMZN** | `#ffb347` | Click to load |
| **META** | `#c9a6ff` | Click to load |
| **TSLA** | `#ff6b6b` | Click to load |

**Active pill** = colored border. Scroll horizontally on mobile.

---

### 3. Stock Header (Updates on Ticker Select)

| Field | Example |
|-------|---------|
| **Name** | `NVDA (Nvidia)` |
| **Meta** | `XLK · QA` |
| **Price** | `$452.31` |
| **Price Label** | `LAST` |

---

### 4. Regime Cards (3 Cards — One Per Regime)

Each card shows:
- **Label** (TRENDING / MEAN_REV / CRISIS)
- **Description** (from `REGIME_META.desc`)
- **Days in Window** (last 90 days count)

Color-coded left border matches regime color.

---

### 5. Charts Grid (2-Column on Desktop ≥768px, 1-Column Mobile)

| Chart | Type | Key Visuals |
|-------|------|-------------|
| **Price & Regime** (full width) | Line + colored regime segments | Regime-colored line, clickable timeline bar below |
| **RSI (14)** | Histogram | 🟢 Green <30 (oversold) · 🔴 Red >70 (overbought) · Grey 30–70 |
| **CCI (20) + Signal Dots** | Line + scatter | Signal dots: 🟢 BUY · 🔴 SHORT/SELL · 🟡 EXIT · 🔵 HOLD · ⚪ FLAT · 🟣 WATCH |
| **Signal History** (full width) | Horizontal bar chart | Click any bar to inspect |

**Price Chart Timeline Bar** (below price chart):
- Colored segments = regime timeline
- **Click any segment** → tooltip with date, regime, price, RSI, CCI, signal

---

### 6. Signal Legend (Below Signal History)

| Dot Color | Label | Meaning |
|-----------|-------|---------|
| `#76e4c4` | BUY | Long entry |
| `#ff6b6b` | SHORT / SELL | Short entry / Long exit |
| `#ffd166` | EXIT | Close position |
| `#7ec8e3` | HOLD LONG | Stay long |
| `#444` | FLAT | No position |
| `#c9a6ff` | WATCH | Monitoring, no action |

---

### 7. Strategy Rules Table

Auto-populated from backend (`/api/ticker` → `strategy_rules`):

| Regime | Entry | Exit | Size | Direction |
|--------|-------|------|------|-----------|
| **TRENDING** | CCI crosses above +100 | CCI drops below 0 | 100% | LONG |
| **MEAN_REV** | RSI < 30 / RSI > 70 | RSI returns to 45–55 | 60% | BOTH |
| **CRISIS** | CCI < -250 (extreme only) | Regime change | 10% | SHORT/FLAT |

---

### 8. Active Strategy Card (Bottom)

Shows **real-time snapshot** for selected ticker:

| Field | Source |
|-------|--------|
| **Date** | Most recent bar |
| **Regime** | Current regime label + color |
| **RSI 14** | Value + color (green/red/grey) |
| **CCI 20** | Value |
| **Active Signal** | BUY / SELL / EXIT / HOLD / FLAT / WATCH |
| **Rule Text** | Human-readable rule that fired |

**Button**: `⚡ AI REGIME ANALYSIS` — placeholder (triggers alert, backend not yet implemented).

---

### 9. Keyboard / Interaction Shortcuts

| Action | How |
|--------|-----|
| Switch ticker | Click pill or press number key 1–7 (if implemented) |
| Inspect regime | Click any segment on **Price Chart** timeline bar |
| Inspect signal | Click any dot on **CCI chart** or **Signal History bar** |
| Refresh macro | Auto-refreshes every 5 min; manual: refresh page |
| View all MAG7 | Click `Run All` via `/api/run_all` (not directly in UI) |

---

### 10. API Endpoints (For Programmatic Use)

| Endpoint | Method | Params | Returns |
|----------|--------|--------|---------|
| `/` | GET | — | Dashboard HTML |
| `/health` | GET | — | Service status, HMM config |
| `/api/macro` | GET | — | `{macro_filter, label, vix_high, vix_low}` |
| `/api/ticker` | GET | `ticker=SYM`, `hmm=0\|1` | `{chart: {...}, performance: {...}}` |
| `/api/run_all` | GET | `hmm=0\|1` | `{results[], summary_table[], aggregate{}, macro_filter, config{}}` |

**Example**:
```bash
# Single ticker with HMM (default)
curl "http://localhost:9229/api/ticker?ticker=NVDA"

# Disable HMM (rule-based fallback)
curl "http://localhost:9229/api/ticker?ticker=NVDA&hmm=0"

# Batch all 7
curl "http://localhost:9229/api/run_all"
```

---

### 11. Configuration Knobs (Top of `qa_server.py`)

| Constant | Default | Purpose |
|----------|---------|---------|
| `HMM_STATES` | 3 | Number of HMM regimes |
| `HMM_ENSEMBLE_N` | 5 | Ensemble size |
| `HMM_ITERATIONS` | 2000 | Max EM iterations |
| `PERSISTENCE_DEFAULT` | 3 | Base persistence bars |
| `CCI_ENTRY` | 100 | CCI breakout threshold |
| `CCI_EXIT` | 0 | CCI exit threshold |
| `CCI_SHORT` | -250 | CRISIS short threshold |
| `RSI_OVERSOLD` | 30 | MEAN_REV buy |
| `RSI_OVERBOUGHT` | 70 | MEAN_REV sell |
| `POSITION_CRISIS` | 0.10 | CRISIS position size |
| `MAX_DRAWDOWN` | -0.15 | Circuit breaker |
| `VIX_HIGH` | 25 | RISK_OFF threshold |
| `VIX_LOW` | 15 | RISK_ON threshold |
| `LOOKBACK_DAYS` | 180 | Data window |

---

### 12. Running the Service

```bash
# QA (port 9229)
cd /Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-2_QA
python3 qa_server.py

# PROD (port 9228) — same code, different PORT env
PORT=9228 python3 qa_server.py

# Via launchd (auto-restart)
launchctl load ~/Library/LaunchAgents/com.ninestreet.ns2.qa.plist
```

---

### 13. Known Issues / TODOs

| Issue | Status |
|-------|--------|
| **AI Regime Analysis button** | Placeholder only — no backend yet |
| **No auth** | Open on LAN; add reverse proxy + auth for prod |
| **Single-threaded HTTP server** | `http.server` — not for high concurrency |
| **yfinance rate limits** | Batch calls may hit limits; consider caching layer |
| **No unit tests** | Coverage = 0% (see `TEST_COVERAGE_REVIEW.md`) |

---

## Quick Reference: Regime → Signal Mapping

```
┌──────────────┬─────────────────────────────────────────────────────────────┐
│ REGIME       │ SIGNAL LOGIC                                                │
├──────────────┼─────────────────────────────────────────────────────────────┤
│ TRENDING (0) │ CCI > +100 → BUY (100%)                                     │
│              │ CCI < 0 → EXIT                                             │
│              │ Vol ratio > 3 → FLAT                                       │
├──────────────┼─────────────────────────────────────────────────────────────┤
│ MEAN_REV (1) │ RSI < 30 → BUY (60%)                                       │
│              │ RSI > 70 → SELL                                            │
│              │ RSI 45–55 → EXIT                                           │
│              │ Vol ratio > 3 → FLAT                                       │
├──────────────┼─────────────────────────────────────────────────────────────┤
│ CRISIS (2)   │ CCI < -250 → SHORT (10%)                                   │
│              │ Else → FLAT                                                │
│              │ Entering CRISIS while LONG → FORCE EXIT                    │
└──────────────┴─────────────────────────────────────────────────────────────┘
```

---

## Files in This Package

| File | Purpose |
|------|---------|
| `qa_server.py` | Full backend (HMM, signals, backtest, HTTP server) |
| `ns2_dashboard.html` | Single-file dashboard (Chart.js, no build step) |
| `NS2_ENHANCEMENTS_AND_UI_GUIDE.md` | This document |

---

*End of document*