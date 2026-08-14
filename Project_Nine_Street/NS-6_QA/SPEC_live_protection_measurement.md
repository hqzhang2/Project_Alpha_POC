# NS-6 Phase-2 Hardening Spec — Live Protection, Measurement & Operations

**Frontier-owned spec.** Junior implements wiring/tests/UI only; never changes
thresholds, formulas, or the money path. Flags discrepancies, does not resolve them.

**Scope:** the gaps from the fund-manager assessment after R1–R4 (R5 deferred to review v3).
Ordered by capital-preservation impact. All thresholds already live in `config.py` THETA_DEFAULTS
unless noted; **do not add new magic numbers** — read them from theta.

**Baseline gate:** `env -i HOME=$HOME py3.9 -m pytest NS-6_QA/tests/ -q` must stay green (204 → grows).
Canonical python: `/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3`.

---

## G1 — Wire circuit breakers & position stops into the live loop  (Tier 1, highest priority)

**Goal:** the hard floor and per-position stops must FIRE on real data, not return empty lists.

### Current state
- `enforcement.check_circuit_breakers(current_dd, budget_pct, position_drawdowns, cross_sectional_corr, theta)`
  and `check_position_stops(position_drawdowns, asset_classes, theta)` and
  `check_reentry_hysteresis(...)` are pure and correct but `_enforcement_status()` never calls them
  with real inputs — it emits `circuit_breakers: [], position_stops_triggered: []`, `protective_puts: None`.
- `store.log_circuit_breaker(type, ticker, detail)` + `query_breakers()` + `circuit_breaker_log` table exist.

### Methodology (frontier-owned — do not change)
- **Hard floor** (already sign-corrected in `check_circuit_breakers`):
  triggered when `current_dd <= -(hard_floor_trigger * |budget_pct|)`, default `hard_floor_trigger=0.90`.
- **Systemic event**: `pct_breached >= systemic_event.pct_positions (0.60) AND cross_sectional_corr > corr_threshold (0.70)`,
  where `pct_breached = fraction of positions with per-ticker dd < pct_threshold (-0.15)`.
  `cross_sectional_corr` = mean off-diagonal correlation of the trailing 60d daily returns across holdings.
- **Position stops**: per-ticker dd (from RUNNING PEAK, sign = negative fraction) `<=` the asset-class threshold
  (`equity -0.25`, `bond_etf -0.15`, `commodity_etf -0.20`, `cash_proxy -0.05`, `unknown -0.20`).
- **Re-entry hysteresis**: `check_reentry_hysteresis(last_breaker_time, last_stop_times)` blocks re-entry
  within `breaker_reentry_days=5` / `position_stop_reentry_days=20` (trading days → the code already converts).

### Required wiring (junior)
1. **Per-ticker drawdown** — add `budget.compute_drawdown` per ticker from the closes `price_feed` already
   fetches. Extend `price_feed.compute_snapshot` (or a new `enforcement` helper) to return
   `{ticker: drawdown}` and the trailing 60d returns matrix for correlation. **Reuse `price_feed`'s cached closes** —
   no second fetch.
2. **Asset-class map** — classify each holding ticker: `BIL/SPY/TLT/GLD/IEF/DBC` → `cash_proxy`/equity-ETF/`bond_etf`/
   `commodity_etf`; everything else → `equity`. Put the map in `config.py` (frontier-owned constant `ASSET_CLASSES`).
3. **`_enforcement_status()`** — call `check_circuit_breakers(current_dd, budget_pct, position_drawdowns, corr, theta)`
   and `check_position_stops(position_drawdowns, asset_classes, theta)` with the real inputs; surface the lists in the
   JSON (already keyed `circuit_breakers`, `position_stops_triggered`). Call `check_reentry_hysteresis` using
   `store.query_breakers()` timestamp + a new `store` last-stop-timestamp, surface as `reentry_blocked`.
4. **Persist on fire** — when a breaker/stop `triggered` is True, `store.log_circuit_breaker(...)` once per event
   (dedupe: don't re-log the same breaker on consecutive polls — track "already logged" via the log's last row).
5. **Dashboard** — the Enforcement Events panel already renders `circuit_breakers` / `position_stops_triggered` /
   `last_breaker_time`; just wire `reentry_blocked` + ensure non-empty lists render (no JS change expected beyond
   `reentry_blocked`).

### Acceptance
- Test with a REAL breach (hard floor fires) and a REAL non-breach (clean) — the sign-bug guard.
- A systemic event (≥60% positions < −15% AND corr > 0.7) fires the systemic breaker.
- A position down ≥25% from its running peak triggers its stop.
- Breaker event is persisted exactly once and surfaced; `reentry_blocked` reflects the 5-day window.

---

## G2 — Live performance scoreboard + backtest-vs-live reconciliation  (Tier 2)

**Goal:** the PM can see whether the LIVE book is beating its bar, with attribution, and whether live matches backtest.

### Methodology (frontier-owned)
- **Daily portfolio return** is already derivable from `price_feed`'s NAV series. Persist a daily
  `{date, nav, ret}` row (new table `performance_log` or extend `drawdown_log`).
- **Benchmark = SPY** as calibration + the **held universe** (equal-weight of the current book's tickers) as the
  honest bar (R5). Label both; do NOT hardcode "beat SPY" as a gate.
- **Metrics (trailing 21/63/252 trading days + since-inception):**
  - Total return = `NAV_t / NAV_0 - 1`
  - Annualized return = `(NAV_t/NAV_0)^(252/n) - 1`
  - Vol = `std(daily_ret) * sqrt(252)`
  - Sharpe = `mean(ret)/std(ret) * sqrt(252)` (rf=0)
  - Max drawdown (running peak) — reuse `budget.compute_drawdown`
  - Excess vs each benchmark (port ret − bench ret) over the window
- **Attribution**: per-ticker contribution = `Σ_t (w_i,t * r_i,t)` summed over the window; report top contributors/detractors.
- **Reconciliation**: overlay the live realized NAV path against the walk-forward backtest NAV for the same dates
  (backtest already produces a NAV series in `ns6_backtest.py`). Report `live_total_ret`, `backtest_total_ret`,
  `delta_pp`, and a simple divergence flag when `|delta_pp|` exceeds a config threshold (`reconcile_divergence_pp`, frontier adds to theta).

### Required wiring (junior)
1. Extend `price_feed` to persist a daily NAV/return row (idempotent on date).
2. New `performance.py` (or `metrics.py`) with the pure metric functions above (read from the persisted series).
3. New endpoint `GET /api/performance` returning trailing metrics + attribution + reconciliation.
4. Dashboard panel "Performance & Attribution" (dense, matches house style).
5. Reconciliation harness reads the backtest NAV (reuse `ns6_backtest.simulate` or a precomputed baseline JSON).

### Acceptance
- Trailing 21/63/252d metrics correct on a synthetic NAV series (hand-computed fixture).
- Attribution sums to total return (within rounding).
- Reconciliation reports a sane `delta_pp` on a synthetic live-vs-backtest pair.

---

## G3 — Live options chain → put overlay & covered-call gate  (Tier 1)

**Goal:** puts and covered calls are priced off the real chain, not proxy numbers.

### Methodology (frontier-owned, already resolved)
- Put overlay: `put_notional = nav * multiplier * coverage_pct`; annual cost = `monthly_premium * 12 * multiplier * coverage`.
  Gate: evaluate when `multiplier < protective_puts.gate_multiplier (0.80)`.
- Covered call: overwrite % by multiplier band (`full 0.50 / reduced 0.25 / none 0.00`), delta target `0.25`, DTE 30–45.
- **Do NOT change these formulas** — only swap the price source from proxy to live.

### Required wiring (junior)
1. A_T exposes `/api/options?ticker=X` (already live, `options.get_options_chain`). NS-6 fetches it
   server-side (new `options_feed.py`) to get the put/call mid price for the requested DTE/delta.
2. Replace the proxy price inputs in `options.py` (put premium, call premium) with the live chain mid.
   Fail-open: chain unavailable → the existing proxy fallback, with a `pricing_source: "live"|"proxy"` field surfaced.
3. `_enforcement_status()` emits `protective_puts` with the live premium + `pricing_source`.

### Acceptance
- `options.py` computes put/call economics from a mocked live-chain payload (no network in tests).
- `pricing_source` flips to `"proxy"` when the chain is empty/unavailable (fail-open).

---

## G4 — Live lot-level cost basis + cash position  (Tier 3)

**Goal:** tax-aware funding paths use the PM's actual lots/basis; cash is a tracked position.

### Methodology (frontier-owned)
- Lot-level basis comes from NS-5's portfolio store `{ticker: {shares, account, lots}}`. NS-6 already normalizes
  shares→weights in `price_feed.resolve_holdings`; **add** `lots` passthrough (`{ticker: [{qty, basis, acquired}]}`).
- `tax_context.py` ranks funding paths by after-tax cost; feed it the real lots instead of synthetic ones.
- Cash: NAV = `Σ shares_i × price_i + cash`. `cash` comes from the portfolio store (default `0.0` when absent).

### Required wiring (junior)
1. `price_feed.resolve_holdings` returns a 4th value (`lots`) alongside weights/shares; NAV adds `cash`.
2. `tax_context` consumes real lots; fall back to current proxy when the store has no `lots` (fail-open).
3. `_portfolio_get` / dashboard composition modal shows cash + total NAV.

### Acceptance
- A portfolio with `{shares, lots, cash}` produces a NAV including cash and a tax ranking using its lots.
- A portfolio WITHOUT `lots` still works (proxy fallback) — no crash.

---

## G5 — Alerting on enforcement events  (Tier 3)

**Goal:** a hard-floor breach / crisis entry / URGENT drift reaches the PM without them polling the dashboard.

### Required wiring (junior)
1. `store` already persists breaker events; add a simple `notify` path: when a NEW breaker/stop/CRISIS-entry fires,
   append a line to `logs/ns6_alerts.log` (timestamp + type + detail). Keep it a file — no external service in this phase.
2. Add a dashboard "unread alerts" badge that lights when `query_breakers()` has rows newer than the last-viewed
   timestamp (persist last-viewed in `store` settings).
3. Optional (flag for PM): wire the same file to an email notifier later.

### Acceptance
- A triggered breaker writes exactly one alert line; repeated polls do NOT duplicate.
- Dashboard badge shows unread count and clears on view.

---

## Work-split guardrails (junior MUST follow)
- Read all thresholds from `config.py` THETA_DEFAULTS; never hardcode.
- Do NOT modify `budget.py`, `enforcement.py` money-path formulas, or backtest/signal code — only call them.
- Sign-bug guard: every drawdown/budget comparison compares same-sign values; test a REAL breach AND a real non-breach.
- Dollars-vs-fraction guard: any adjustment added to a return is a fraction of NAV, never an absolute $.
- Hermetic tests: monkeypatch `store.DB_PATH` (temp), network seams (yfinance/options), never hit live data.
- Commit to `feature/*` (never master/main). Run the full suite before each commit.
