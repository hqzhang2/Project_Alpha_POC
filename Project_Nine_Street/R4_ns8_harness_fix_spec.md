# R4 — NS-8 Walk-Forward Harness Fix Spec

**Status:** Spec for implementation (not implemented)
**Date:** August 2026
**Source:** `full_stack_review_v3.md` §13 R4 · Phase 0 in `v3_execution_plan.md`
**Owner:** Frontier (stats/methodology — this is backtest math, not junior plumbing)
**Prerequisite for:** R8 (inverse-vol sizing) and R1 (combined-fund walk-forward).

---

## 1. Problem

`NS-8_QA/walkforward.py` produces numbers that are **not trustworthy**, and the
NS-8 acceptance gate (Sharpe ≥ 0.60, MaxDD ≤ 15%, etc.) was evaluated against
them. Specifically, the current output is internally inconsistent:

| Metric | Reported | Why it is implausible |
|---|---|---|
| Sharpe | 1.19 | Higher than Faber (1.06) *and* Concretum (0.68), but… |
| CAGR | 3.10% | …the *lowest* of all three — Sharpe 1.19 with 3.1% CAGR implies ~2.5% vol, absurd for a book holding equities/commodities/REITs |
| Annual cost drag | 0.0001 (~1 bp) | vs Concretum's ~22 bp and the monthly fixed-day ~41 bp — effectively cost-free |
| Annual turnover | 13.60% | vs the 0.8% gate — but the gate was written for *tranched weekly*, which the harness never simulates |

## 2. Root Cause (confirmed from reading `walkforward.py`)

The harness has four defects, ranked:

### 2.1 🔴 CRITICAL — the walk-forward runs on synthetic data

`load_historical_prices()` does **not** load real history. It fabricates prices:

```python
np.random.seed(42)
...
ret = np.random.normal(base_ret, vol)
price *= (1 + ret)
```

The comment in the source admits it ("In production, replace with actual
historical data load") but it was **shipped to PROD anyway**. The entire
walk-forward — and therefore the NS-8 acceptance gate — was evaluated on a
seeded random walk, not 2006–2026 markets. **Every downstream number (Sharpe,
CAGR, MaxDD, turnover, cost drag) is meaningless until this is fixed.** This
alone invalidates NS-8's "gate PASS."

### 2.2 🔴 The tranching is never simulated

- `run_walkforward(tranched=True)` accepts a `tranched` parameter and **never
  uses it** — there is no tranching logic anywhere in the loop.
- `compare_tranched_vs_monthly()` is a stub returning
  `{"note": "Requires daily data for full implementation"}`.

The Concretum innovation (tranched weekly rebalancing, 220→63 bps timing-luck
reduction) is the *headline* of NS-8's design and is **absent from the harness**.
The turnover gate (≤0.8%) was written for tranched weekly, so the harness's
monthly 13.6% is being compared against the wrong denominator.

### 2.3 🟠 Cost-drag / turnover unit mismatch

With `TXN_COST_BPS = 10` and monthly turnover ~1.13%, the per-month cost drag is
~1.1 bp, annualizing to ~1.4 bp — an order of magnitude below the design's
Concretum thresholds (~22–41 bp). This is partly a symptom of 2.1/2.2 (wrong
turnover from synthetic data, no tranching), but the cost model must also be
reconciled with the design's numbers once real data lands.

### 2.4 🟡 Sharpe annualization assumption

`sharpe = mean(returns)/std(returns) * sqrt(12)` assumes **monthly** returns.
Correct only if the harness produces monthly returns; must be re-validated when
daily real data and tranching land (tranched weekly rebalancing produces
weekly-frequency return streams).

## 3. The Fix

### 3.1 Replace synthetic data with real historical data

Load real 2006–2026 daily closes for the six ETFs (SPY, EFA, IEF, VNQ, DBC, SHV).
Two sources are already in the stack:

- **yfinance** (default in `config.DATA_SOURCE`) — fetch daily adjusted closes,
  cache locally for reproducibility.
- **Polygon** (optional, `POLYGON_API_KEY`) — for adjusted-close accuracy.

**Pattern to mirror:** NS-7's `ns7_walkforward.py` already does real-data
walk-forward with a local close cache (`data/spy_closes.json`,
`data/bench_closes.json`). NS-8 should follow the same pattern, **not**
NS-8's current synthetic loader.

```python
def load_historical_prices(tickers, start, end):
    """Real daily adjusted closes, cached to data/prices_cache.pkl (tz-naive)."""
    # yfinance .download(tickers, start, end, auto_adjust=True)
    # -> {ticker: pd.Series of daily closes}, tz-normalized, cached.
    # Fail-open: a ticker with no data is dropped with a warning, not fabricated.
```

**No synthetic fallback.** A missing ticker must be reported, not invented.

### 3.2 Implement tranched-weekly rebalancing

Simulate the Concretum 4-tranche schedule on **daily** data:

- Split the book into 4 sub-portfolios (25% each).
- Tranche *t* rebalances in week *t* of each month.
- Each tranche holds its in-trend assets at 5% each (25% × 20%).
- The monthly signal (200-day SMA) is computed at month-end and applied by each
  tranche in its scheduled week.

This replaces the current single monthly rebalance and directly addresses the
turnover gate (which should then land near the design's ~0.6%).

### 3.3 Fix the cost/turnover accounting

- Apply `TXN_COST_BPS` per round-trip on actual tranche turnover (daily).
- Reconcile `annual_turnover` and `annual_cost_drag` against the design's
  Concretum thresholds (~0.6% and ~22 bp). If they don't land there, that is a
  *finding*, not a rounding error — surface it to the PM rather than silently
  passing.

### 3.4 Re-validate the Sharpe annualization

With daily data and tranched rebalancing, compute Sharpe from the actual return
frequency (weekly), or annualize correctly from daily returns
(`mean/std * sqrt(252)`). Do not assume monthly.

## 4. Acceptance

| Criterion | Check |
|---|---|
| Real data | `walkforward.py` reports a window of real 2006–2026 closes (no `np.random.seed`) |
| Tranching simulated | `tranched=True` actually changes the equity curve vs monthly; turnover lands near the design's ~0.6% |
| Plausible Sharpe/CAGR | Sharpe and CAGR are mutually consistent (e.g., a ~0.6 Sharpe with ~6% CAGR and ~10% vol — in line with Concretum, not 1.19/3.1%) |
| Cost drag non-trivial | ~20–40 bp/yr, not ~1 bp |
| Gate re-evaluated | The NS-8 acceptance gate is re-run on real numbers; a FAIL is a valid, reportable outcome |
| Deterministic | Seeded only for *tests*, never for the production walk-forward |

## 5. Files Touched

- `NS-8_QA/walkforward.py` — replace `load_historical_prices` (real data),
  implement tranching, fix cost/turnover + Sharpe.
- `NS-8_QA/config.py` — add daily-data cache path, tranche constants already
  present (`TRANCHES=4`, `TRANCHE_WEEK=[1,2,3,4]`).
- `NS-8_QA/tests/` — new tests for the tranche scheduler and the real-data loader.
- `NS-8_PROD/walkforward.py` — mirror after QA sign-off (not before).

## 6. Tests

```python
# tests/test_walkforward.py
def test_load_historical_prices_returns_real_series():
    p = load_historical_prices(["SPY", "IEF"], "2020-01-01", "2021-01-01")
    assert "SPY" in p and len(p["SPY"]) > 200     # ~252 trading days
    assert p["SPY"].iloc[0] != 100.0               # not the synthetic seed

def test_tranched_differs_from_monthly():
    m = run_walkforward(tranched=False)["metrics"]
    t = run_walkforward(tranched=True)["metrics"]
    assert t["annual_turnover"] < m["annual_turnover"]   # tranching reduces turnover

def test_cost_drag_is_material():
    m = run_walkforward()["metrics"]
    assert m["annual_cost_drag"] > 0.0005          # > 5 bp/yr, not ~1 bp

def test_sharpe_cagr_consistent():
    m = run_walkforward()["metrics"]
    # A Sharpe ~0.6 with CAGR ~6% is consistent; Sharpe 1.19 with 3.1% is not.
    # Assert the implied vol is in a sane band (5%–15%), not ~2.5%.
    implied_vol = m["cagr"] / m["sharpe"] if m["sharpe"] else float("inf")
    assert 0.05 <= implied_vol <= 0.15
```

## 7. Risks

| Risk | Mitigation |
|---|---|
| Real 2006–2026 data shows NS-8 *fails* the gate | That is the point of R4 — a real FAIL is a valid finding, not a bug to patch around. It informs R8 and the PM's sizing decision |
| yfinance adjusted-close / survivorship issues | Use `auto_adjust=True`; Polygon (already in the stack) for cross-check if available |
| Tranched simulation is complex | Port the Concretum tranche logic faithfully (4 tranches, weekly offset); validate against the paper's ~0.6% turnover as a sanity check |
| Synthetic-data habit creeps back in tests | Tests may seed for determinism, but `load_historical_prices` must never seed in the production path |

---

## 8. Why This Is Phase 0 (not deferred)

R4 is the **precondition for the entire critical path** (R8 → R3 → R1 → R2 → R5).
The inverse-vol enhancement (R8) and the combined-fund walk-forward (R1) both
need a harness whose vol/return math is correct. Fixing the harness first means
every downstream number is trustworthy; skipping it means every downstream
number is fiction. It is independent of R6 (parallel), and is a fix rather than
new methodology.
