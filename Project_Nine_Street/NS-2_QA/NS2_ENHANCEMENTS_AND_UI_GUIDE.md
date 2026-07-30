# NS-2 MAG7 HMM Regime Strategy — Enhancements & UI Guide

> **Project**: Project Alpha POC / Nine Street  
> **Service**: NS-2 QA (port 9229) | NS-2 PROD (port 9228)  
> **Branch**: `feature/v2.1`  
> **Last Updated**: July 30, 2026 — Phase 3 complete

---

## Part 1: Enhancement Summary (Post-Phase 3 — Current State)

The v2 pipeline implements a **3-state HMM regime detector** with ensemble voting,
asset-class parameter profiles, momentum-aware signal logic, confidence-weighted
sizing, trailing stops, and a walk-forward backtest harness for honest OOS evaluation.

### Architecture (qa_server.py)

```
fetch_ohlcv(ticker, 750d) → add_rich_features(8 cols)
  → get_regimes(profile=asset_class)    [HMM ensemble or rule-based fallback]
  → generate_signals_v2(profile=...)    [regime-specific + momentum short-ban]
  → apply_stops                        [trailing ATR stop]
  → backtest → apply_dd_breaker        [drawdown circuit breaker]
  → add_signal_labels_v2               [signal-derived labels, hooked to backtest array]
  → performance_summary                [long+short trade counting]
```

### Key Parameters (Module-Level Constants)

| Constant | Value | Phase |
|---|---|---|
| `LOOKBACK_DAYS` | 750 (was 180) | Phase 2 |
| `HMM_COVARIANCE` | `"diag"` (was `"full"`) | Phase 2 |
| `HMM_STATES` | 3 | Phase 1 |
| `HMM_ENSEMBLE_N` | 5 | Phase 1 |
| `PERSISTENCE_DEFAULT` | 3 | Phase 1 |
| `CCI_ENTRY / CCI_EXIT` | 100 / 0 | — |
| `RSI_OVERSOLD / RSI_OVERBOUGHT` | 30 / 70 | — |
| `POSITION_CRISIS` | 0.10 | — |
| `MAX_DRAWDOWN` | −0.15 | — |

### Asset-Class Profiles (Phase 2)

| Class | Example Tickers | vol_crisis | vol_trend | trend_threshold | cci_short |
|---|---|---|---:|---:|---:|---:|
| equity | AAPL, NVDA, MU | 0.030 | 0.012 | 0.030 | −250 |
| bond | TLT, IEF, AGG | 0.012 | 0.006 | 0.015 | −200 |
| commodity | GLD, USO | 0.022 | 0.009 | 0.022 | −225 |

### Phase 3 Signal Enhancements

- **Momentum short-ban**: MEAN_REV short (RSI>70) blocked when price > 50MA unless macro=RISK_OFF; shorts below 50MA still allowed. Fixes MU/TSLA fade-the-uptrend bleed.
- **Confidence-weighted sizing**: ensemble agreement scales exposure 0.5×–1.0× per regime base size.
- **Trailing ATR stops**: stop ratchets up with high-water close, never down (was anchored to entry price).
- **Labels from signal array**: `add_signal_labels_v2` derives labels from the actual signal column (post-stops, post-persistence), not independently from RSI/CCI thresholds. Pill = chart = backtest position.

### HMM State Mapping

| State | Label | Color | Description |
|---|---:|---:|---|
| 0 | TRENDING | `#22c55e` (green) | CCI breakout + ADX confirmation |
| 1 | MEAN_REV | `#7ec8e3` (blue) | RSI fade + Bollinger confirmation |
| 2 | CRISIS | `#ff6b6b` (red) | Capital preservation |

### 8 Features

```
log_return, rolling_vol, vol_ratio, bb_position, adx, ma_distance, atr_ratio, volume_z
```

### Signal Color Map (pill = dashboard = legend = bars = prompt)

| Label | Color |
|---|---|
| BUY | `#22c55e` |
| SELL | `#ff6b6b` |
| SHORT | `#ff6b6b` |
| EXIT | `#ffd166` |
| HOLD LONG | `#7ec8e3` |
| FLAT | `#444` |
| WATCH | `#c9a6ff` |

---

---

## Part 2: Walk-Forward Results (Phase 3 — July 30, 2026)

Honest OOS evaluation via `ns2_backtest.py` (3y, 10bps costs, causal HMM):

| Ticker | OOS Ret% | Sharpe | Win% | PF | Verdict |
|---|---:|---:|---:|---:|---|
| GOOGL | +52.8 | 2.68 | 88.9 | 15.01 | **PASS** |
| MSFT | +19.8 | 2.34 | 100.0 | ∞ | **PASS** |
| MU | +43.4 | 0.76 | 55.6 | 2.66 | MARGINAL |
| META | +11.4 | 0.57 | 47.1 | 1.69 | MARGINAL |
| NVDA | +8.2 | 0.47 | 30.0 | 1.45 | MARGINAL |
| TLT | −2.9 | −0.39 | 46.2 | 1.17 | MARGINAL |
| AAPL | −10.0 | −0.46 | 43.8 | 0.75 | NO-EDGE |
| AMZN | −9.1 | −0.43 | 28.6 | 0.83 | NO-EDGE |
| TSLA | −26.9 | −1.41 | 25.0 | 0.06 | NO-EDGE |

**Aggregate**: avg OOS ret +9.7%, avg Sharpe +0.46, win rate 51.7%, 2/9 PASS.

Acceptance gates: PF ≥ 1.5 AND Sharpe ≥ 1.0 → PASS; PF ≥ 1.0 → MARGINAL; else NO-EDGE.

Re-run: `python3 ns2_backtest.py --years 3 --out ns2_walkforward_results.json`

---

## Part 3: Dashboard UI Guide

> **URL**: `http://localhost:9229/` (QA)  
> **Dashboard File**: `Project_Nine_Street/NS-2_QA/ns2_dashboard.html`

### Config Strip (Top)

Shows live config from `/api/config`: asset classes (`equity/bond/commodity`), HMM covariance type (`diag`), lookback window (`750d`). Values in Monaco/monospace, 10px, `var(--sub)`.

### 1. Header & Macro Badge

- **Title**: `HMM Regime Strategy`
- **Macro Badge**: VIX regime: RISK_ON / NEUTRAL / RISK_OFF (with thresholds)
- **QA Badge**: `QA :9229`

### 2. Ticker Pills

MAG7 + any watchlist additions. Active pill gets a colored border. Signal-colored pills from cached signals.

### 3. Charts

| Chart | Content |
|---|---|
| **Price** | Close line `#4ade80`, regime-colored timeline bar below |
| **RSI (14)** | Histogram: green <30, red >70, grey 30–70 |
| **CCI (20)** | Line with signal-colored dots |
| **Signal History** | Horizontal bar, colors from `SIGNAL_COLORS` map |
| **Regime Timeline** | Segments, clickable; hover cross-references inspectPrompt |

### 4. Strategy Rules & Active Card

Auto-populated from `/api/ticker`. Active card shows current close, RSI, CCI, regime, signal.

---

## Part 4: API Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | Dashboard HTML |
| `/health` | GET | `{status, port, hmm_ensemble, features}` |
| `/api/macro` | GET | VIX/SPY macro filter |
| `/api/config` | GET | `{lookback_days, hmm_covariance, asset_profiles, ...}` |
| `/api/backtest` | GET | Latest walk-forward results (404 if not run) |
| `/api/ticker?ticker=SYM` | GET | `{chart, performance}` |
| `/api/run_all` | GET | Batch all watchlist tickers |
| `/api/watchlist` | GET/POST | Manage watchlist |

---

## Part 5: Walk-Forward Harness

```bash
cd /Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-2_QA
python3 ns2_backtest.py --years 3 --out ns2_walkforward_results.json
python3 ns2_backtest.py --tickers TLT MU --years 4 --cost-bps 15 --rule-based
```

Then `/api/backtest` serves the JSON. Results feed into `/api/config` to display next to ticker pills.

---

## Part 6: Tests

```bash
python3 -m pytest test_ns2_signals.py -v   # 33 tests, <2s, no network
```

Covers: `classify_asset`, `add_signal_labels_v2`, `apply_stops` (trail fire/no-fire), `performance_summary` (long+short), `bt.verdict` (all branches), momentum short-ban (3 branches), confidence sizing.

---

## Part 7: Running the Service

```bash
# development (manual)
cd /Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-2_QA
python3 qa_server.py

# QA (launchd-managed)
launchctl kickstart -k gui/$(id -u)/com.ninestreet.ns2.qa

# PROD
PORT=9228 python3 qa_server.py
```

---

## Files

| File | Purpose |
|---|---|
| `qa_server.py` | Full pipeline (HMM, signals, backtest, HTTP server) |
| `ns2_dashboard.html` | Single-file dashboard (Chart.js v4.4) |
| `ns2_backtest.py` | Walk-forward harness (standalone, no server dependency) |
| `test_ns2_signals.py` | Unit tests (33 tests, pure functions) |
| `ns2_walkforward_results.json` | Latest walk-forward output |
| `ns2_watchlist.json` | Watchlist state |
| `ns2_signal_cache.json` | Signal cache |
| `NS2_ENHANCEMENTS_AND_UI_GUIDE.md` | This document |

