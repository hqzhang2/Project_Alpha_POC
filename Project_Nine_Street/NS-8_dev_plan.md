# NS-8 Development Plan

**Status:** Approved for Implementation  
**Target:** Phase 1 scaffold → paper trading in 3 weeks, PROD week 4–5  
**Owner:** Frontier (spec), Junior (implementation)

---

## Phase Overview

| Phase | Duration | Deliverable | Success Criteria |
|-------|----------|-------------|------------------|
| **1** | 2 days | Scaffold + config + signals.py | `python3 signals.py` computes 200-day SMA for 6 ETFs |
| **2** | 3 days | Pipeline (fetch → signal → tranche scheduler) | End-to-end monthly signal generation; tranche state persisted |
| **3** | 3 days | Execution (IBKR MOC + basket CSV) | Paper-tradeable orders generated; TWS Basket loads cleanly |
| **4** | 2 days | Walk-forward harness | Matches Concretum OOS: Sharpe ≥0.60, MaxDD ≤15%, spread ≤100 bps |
| **5** | 1 day | QA server (FastAPI, port 9281) | `/api/signals`, `/api/tranche`, `/api/rebalance` respond |
| **6** | 4+ weeks | Paper trading | No execution errors; signal accuracy verified |
| **7** | 1 day | PROD deploy (port 9280) + launchd | Service runs, monitored, documented |

---

## Phase 1: Scaffold + Signals (2 days)

### Directory Setup
```bash
mkdir -p Project_Nine_Street/NS-8_QA/{data,tests}
```

### Files to Create

| File | Purpose | Key Functions |
|------|---------|---------------|
| `config.py` | All thresholds, paths, env | `SMA_WINDOW`, `TRANCHES`, `DATA_SOURCE`, `DB_PATH` |
| `store.py` | SQLite persistence | `init_db()`, `upsert_signal()`, `get_tranche_state()`, `save_audit()` |
| `signals.py` | Pure SMA logic | `compute_sma(closes, window)`, `generate_signals(prices_dict)` |
| `pipeline.py` | (stub) orchestration | `run_refresh()` placeholder |

### Config (`config.py`)
```python
# Signal
SMA_WINDOW = 200
REBALANCE_CADENCE = "monthly"
TRANCHES = 4
TRANCHE_WEEK = [1, 2, 3, 4]

# Universe
RISKY_ASSETS = ["SPY", "EFA", "IEF", "VNQ", "DBC"]
CASH_PROXY = "SHV"
ASSET_WEIGHT = 0.20

# Costs
TXN_COST_BPS = 10

# Data
DATA_SOURCE = "yfinance"
POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")
LOOKBACK_DAYS = 252 + SMA_WINDOW

# Paths
DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "ns8.db"
SIGNALS_PATH = DATA_DIR / "signals.json"
TRANCHE_STATE_PATH = DATA_DIR / "tranche_state.json"
AUDIT_LOG_PATH = DATA_DIR / "audit_log.jsonl"

# WF
WF_START = "2006-01-01"
WF_END = "2026-07-31"
```

### Store (`store.py`)
```python
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS signals (
        as_of TEXT PRIMARY KEY,
        signals_json TEXT,
        weights_json TEXT,
        version INT,
        generated_at TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS tranche_state (
        tranche_idx INT PRIMARY KEY,
        next_rebalance TEXT,
        last_rebalance TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        tranche_idx INT,
        symbol TEXT,
        side TEXT,
        qty REAL,
        order_id TEXT
    )""")
    conn.commit()
    return conn

def upsert_signal(as_of, signals, weights, version, generated_at):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO signals VALUES (?, ?, ?, ?, ?)",
                 (as_of, json.dumps(signals), json.dumps(weights), version, generated_at))
    conn.commit()
    conn.close()

def get_tranche_state():
    # Returns current tranche_idx (0-3) and next rebalance date
    pass
```

### Signals (`signals.py`)
```python
def compute_sma(closes: List[float], window: int) -> Optional[float]:
    """Simple moving average on daily closes (oldest first)."""
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window

def generate_signals(prices: Dict[str, List[float]], window: int = 200) -> Dict[str, int]:
    """Return {ticker: 1|0} binary signal at month-end."""
    signals = {}
    for ticker, closes in prices.items():
        sma = compute_sma(closes, window)
        if sma is None:
            signals[ticker] = 0  # insufficient history → cash
        else:
            signals[ticker] = 1 if closes[-1] > sma else 0
    return signals

def compute_weights(signals: Dict[str, int]) -> Dict[str, float]:
    """20% per signal=1, remainder to SHV."""
    weights = {}
    risky_on = sum(1 for v in signals.values() if v == 1)
    for ticker, sig in signals.items():
        if ticker == "SHV": continue
        weights[ticker] = 0.20 if sig == 1 else 0.0
    weights["SHV"] = 1.0 - sum(weights.values())
    return weights
```

### Test (`tests/test_signals.py`)
```python
def test_sma():
    closes = list(range(1, 201))
    assert compute_sma(closes, 200) == 100.5

def test_signal_generation():
    prices = {
        "SPY": list(range(100, 300)),  # trending up
        "EFA": list(range(200, 0, -1)),  # trending down
        "IEF": [100] * 200,
    }
    signals = generate_signals(prices)
    assert signals["SPY"] == 1
    assert signals["EFA"] == 0
    weights = compute_weights(signals)
    assert abs(sum(weights.values()) - 1.0) < 1e-9
```

---

## Phase 2: Pipeline (3 days)

### `pipeline.py` — Full Refresh

```python
def fetch_prices(tickers: List[str], lookback: int, source: str = "yfinance") -> Dict[str, List[float]]:
    """Returns {ticker: [daily adjusted closes oldest-first]}"""
    if source == "yfinance":
        import yfinance as yf
        end = datetime.now()
        start = end - timedelta(days=lookback + 30)
        data = yf.download(tickers, start=start, end=end, auto_adjust=True, progress=False)
        closes = data["Close"].dropna()
        return {t: closes[t].tolist() for t in tickers if t in closes.columns}
    elif source == "polygon":
        # Polygon.io implementation here (requires API key)
        pass
    raise ValueError(f"Unknown source: {source}")

def is_month_end(date: datetime) -> bool:
    """True if date is last trading day of month."""
    next_day = date + timedelta(days=1)
    return next_day.month != date.month

def run_refresh(as_of: Optional[str] = None, source: str = "yfinance"):
    as_of = as_of or datetime.now().strftime("%Y-%m-%d")
    store.init_db()
    
    # 1. Fetch prices
    tickers = config.RISKY_ASSETS + [config.CASH_PROXY]
    prices = fetch_prices(tickers, config.LOOKBACK_DAYS, source)
    
    # 2. Generate signals
    signals = generate_signals({t: prices[t] for t in config.RISKY_ASSETS})
    weights = compute_weights(signals)
    
    # 3. Tranche scheduling
    tranche_state = store.get_tranche_state()
    # ... compute next tranche rebalance dates ...
    
    # 4. Persist
    doc = {
        "as_of": as_of,
        "signals": signals,
        "weights": weights,
        "version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds")
    }
    store.upsert_signal(as_of, signals, weights, 1, doc["generated_at"])
    config.SIGNALS_PATH.write_text(json.dumps(doc, indent=2))
    store.save_tranche_state(tranche_state)
    
    return doc
```

### Tranche Scheduler Logic
- At startup, initialize 4 tranches with staggered week-1/2/3/4 offsets
- On each monthly rebalance, only the **current tranche** rebalances
- Tranche index advances weekly: `(current_week_of_month - 1) % 4`

---

## Phase 3: Execution (3 days)

### `execution.py`

```python
# Semi-automated: TWS Basket CSV
def generate_basket_csv(doc: dict, aum: float, current_shares: dict) -> str:
    """Returns CSV content for TWS BasketTrader."""
    prices = fetch_latest_prices(doc["weights"].keys())
    rows = ["Symbol,Action,Quantity,OrderType,TimeInForce"]
    for symbol, weight in doc["weights"].items():
        if weight == 0: continue
        target_shares = int((aum * weight) / prices[symbol])
        current = current_shares.get(symbol, 0)
        delta = target_shares - current
        if delta == 0: continue
        side = "BUY" if delta > 0 else "SELL"
        rows.append(f"{symbol},{side},{abs(delta)},MOC,DAY")
    return "\n".join(rows)

# Fully automated: ib_async MOC
async def submit_moc_orders(doc: dict, aum: float, current_shares: dict):
    from ib_async import IB, MarketOrder, Stock
    ib = IB()
    await ib.connect("127.0.0.1", 7497, clientId=8)  # paper: 7497, live: 7496
    
    prices = fetch_latest_prices(doc["weights"].keys())
    orders = []
    for symbol, weight in doc["weights"].items():
        if weight == 0: continue
        target_shares = int((aum * weight) / prices[symbol])
        current = current_shares.get(symbol, 0)
        delta = target_shares - current
        if delta == 0: continue
        contract = Stock(symbol, "SMART", "USD")
        order = MarketOrder("BUY" if delta > 0 else "SELL", abs(delta))
        order.tif = "MOC"
        trade = ib.placeOrder(contract, order)
        orders.append((symbol, delta, trade.order.orderId))
    
    # Wait for fills
    await ib.sleep(2)
    return orders
```

### Audit Logging
```python
def log_audit(tranche_idx: int, symbol: str, side: str, qty: float, order_id: str):
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("INSERT INTO audit_log VALUES (NULL, ?, ?, ?, ?, ?)",
                 (datetime.now().isoformat(), tranche_idx, symbol, side, qty, order_id))
    conn.commit()
```

---

## Phase 4: Walk-Forward Harness (2 days)

### `walkforward.py`

```python
def run_walkforward():
    # Load historical data (cache locally)
    prices = load_historical_prices(config.RISKY_ASSETS + [config.CASH_PROXY],
                                    config.WF_START, config.WF_END)
    
    equity = 1.0
    equity_curve = []
    signals_history = []
    
    for month_end in month_ends_between(config.WF_START, config.WF_END):
        # Get prices up to month_end
        window_prices = {t: prices[t][:prices[t].index.get_loc(month_end)+1] 
                         for t in config.RISKY_ASSETS}
        
        # Generate signal
        signals = generate_signals(window_prices)
        weights = compute_weights(signals)
        
        # Apply tranching (simulate 4-tranche weekly rebalance)
        # For WF: simulate monthly with tranching benefit = reduced timing luck
        monthly_returns = compute_next_month_returns(prices, month_end)
        portfolio_ret = sum(weights[t] * monthly_returns.get(t, 0) for t in weights)
        
        # Transaction costs
        if signals_history:
            turnover = sum(abs(weights[t] - prev_weights.get(t, 0)) for t in weights)
            portfolio_ret -= turnover * config.TXN_COST_BPS / 10000
        
        equity *= (1 + portfolio_ret)
        equity_curve.append(equity)
        signals_history.append(signals)
        prev_weights = weights
    
    # Metrics
    returns = np.diff(equity_curve) / equity_curve[:-1]
    sharpe = np.mean(returns) / np.std(returns) * np.sqrt(12)
    max_dd = max_drawdown(equity_curve)
    
    print(f"Sharpe: {sharpe:.3f}, MaxDD: {max_dd:.2%}, CAGR: {equity**(12/len(equity_curve))-1:.2%}")
    return equity_curve
```

---

## Phase 5: QA Server (1 day)

### `qa_server.py` (FastAPI)

```python
from fastapi import FastAPI, HTTPException
app = FastAPI(title="NS-8 QA")

@app.get("/api/signals")
def get_signals():
    if not config.SIGNALS_PATH.exists():
        raise HTTPException(404, "No signals generated yet")
    return json.loads(config.SIGNALS_PATH.read_text())

@app.get("/api/tranche")
def get_tranche():
    return store.get_tranche_state()

@app.post("/api/rebalance")
def trigger_rebalance(source: str = "yfinance"):
    doc = pipeline.run_refresh(source=source)
    return doc
```

---

## Phase 6: Paper Trading Checklist (4+ weeks)

- [ ] IBKR paper account connected (port 7497)
- [ ] Current holdings entered accurately
- [ ] MOC deadline met daily (submit by 3:30 PM ET)
- [ ] Signal accuracy verified vs. manual calculation
- [ ] Audit log reviewed weekly
- [ ] No execution errors for 4 consecutive weeks
- [ ] Tranche schedule running correctly

---

## Phase 7: PROD Deploy (1 day)

- [ ] `cp -r NS-8_QA NS-8_PROD`
- [ ] Update port to 9280
- [ ] launchd plist: `com.ninestreet.ns8.prod`
- [ ] bootstrap + kickstart
- [ ] Health check: `curl localhost:9280/api/signals`
- [ ] Monitoring alert on stale signals (>2 days)

---

## Dependencies

| Dependency | Status | Notes |
|------------|--------|-------|
| Python 3.9+ | ✓ | |
| yfinance | ✓ | pip install yfinance |
| ib_async | ✓ | pip install ib-async |
| fastapi + uvicorn | ✓ | pip install fastapi uvicorn |
| Polygon API key | Optional | For Phase 2 upgrade |
| IBKR paper account | Required Phase 3+ | TWS/Gateway running |
| SQLite | Built-in | |

---

## Risk Register (from design doc §9)

| Risk | Phase | Mitigation |
|------|-------|------------|
| Data provider outage | 2 | yfinance default; cache 1yr locally |
| IBKR MOC deadline miss | 3 | Submit by 3:30 PM ET; alert |
| Tranche size too small | 2 | Configurable min-AUM; fallback monthly |
| Regime change | 4+ | NS-6 floor; NS-8 is sleeve only |
| SHV yield collapse | 6+ | Monitor; swap to BIL/SGOV |

---

## Acceptance Criteria (from design doc §6)

| Metric | Threshold | Verification |
|--------|-----------|--------------|
| Sharpe (OOS 2006–2025) | ≥ 0.60 | `walkforward.py` output |
| Max Drawdown (OOS) | ≤ 15% | `walkforward.py` output |
| CAGR Spread (tranched vs best day) | ≤ 100 bps | Compare 21-day sim vs tranched |
| Turnover (annual) | ≤ 0.8% | `walkforward.py` output |
| Cost Drag (10 bps) | ≤ 30 bps/yr | `walkforward.py` output |

---

## Timeline

```
Week 1: Phase 1 (scaffold) + Phase 2 (pipeline)
Week 2: Phase 3 (execution) + Phase 4 (WF harness)
Week 3: Phase 5 (QA server) → Paper trading starts
Week 4-7: Paper trading validation (4 weeks minimum)
Week 8: PROD deploy
```

---

**Next Action:** Create `NS-8_QA/` directory and begin Phase 1 scaffold.