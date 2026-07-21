# Code Review (Second Opinion): Project Alpha POC v1.5
**Date:** 2026-07-21  
**Reviewer:** DeepSeek v4 Pro — Senior Quant Engineer  
**Branch:** `release/v1.5` (commit `a3937ed`)

---

## Executive Summary

This is a functional but structurally fragile multi-project monorepo with actively diverging implementations between QA and PROD, duplicated indicator logic across three independent codebases, and several runtime correctness issues below the surface. The quant algorithms are sound in concept but the engineering around them introduces silent failures and data integrity risks.

---

## 🔴 Critical Findings (Data Correctness / Security)

### 1. **mypy imports unused `hmmlearn` — Guaranteed 500 on first request**

`NS-3_PROD/backend/main.py` imports `from hmmlearn.hmm import GaussianHMM` at module level (line 18). This import fires at server startup, not lazily. If `hmmlearn` is not installed in the runtime environment, the entire server crashes with `ImportError` — you cannot even serve `/api/v1/health`.

**Evidence:** Line 18 of `main.py`, plus `qa_server.py` lines 18-25 import `main` at module level.

**Fix:** Either install `hmmlearn` in requirements or wrap in try/except with graceful degradation.

### 2. **ETF_HOLDINGS is stale — Hardcoded top-10 lists from unknown date**

Lines 62-74 of `NS-3_PROD/backend/main.py` hardcode 11 sector ETFs with 10 holdings each. These are not fetched from any data source. Top holdings change quarterly. An ETF like XLK has added/removed holdings since this list was frozen — the algorithm is running on stale constituents.

**Impact:** Tier 3 stock selection may recommend stocks no longer in the target ETF, or miss new large-cap constituents.

**Fix:** Fetch top holdings from yfinance at runtime (`ticker_obj.funds_data.top_holdings`) — the Alpha Terminal already does this in `handle_etf_holdings()`.

### 3. **`linregress` imported but result destructuring is broken**

Line 179: `slope, *_ = linregress(range(len(tail)), tail.values)`

`scipy.stats.linregress` returns a `LinregressResult` namedtuple with fields: `slope, intercept, rvalue, pvalue, stderr, intercept_stderr`. The unpack `slope, *_` will grab `slope` as the `slope` field, then `intercept` as the `*_` catch-all. This **works** but ONLY because `slope` happens to be the first field. If scipy changes field order in a future version, this silently breaks. The intent (discarding everything but slope) is correct but fragile.

### 4. **`ns_backtester.py`, `ns_monte_carlo.py`, etc. — 12 orphaned quant scripts with no tests or validation**

These appear to be the core strategy logic, yet:
- No test coverage
- No docstrings explaining parameters
- No logging — rely entirely on `print()`
- Import each other with file-system relative paths (`sys.path.insert(0, ...)`)

These are production strategy files, not one-off experiments.

### 5. **`get_quotas?tickers=MU` — Alpha Terminal `quotes.py` has 5s cache but no invalidation**

Line 16: `_cache_ttl = 5`. Each ticker is fetched independently with `time.sleep(0.1)` delay (line 141). A 10-ticker request takes 1 second minimum. More importantly, the cache is a module-level global dict with no size limit — a long-running process will accumulate unbounded memory.

### 6. **NS-3 `run_tier3()` — Silent exception suppression**

Line 547-548:
```python
except Exception:
    pass
```

Any error fetching TA data for a stock is silently swallowed. The stock is simply omitted from results with no log, no counter, no indication. A systematic data provider outage produces an empty Tier 3 with no alert.

### 7. **`generate_signal()` in NS-4 has dead comparison**

Line 135:
```python
if row.get('MACD_hist', 0) > 0 and row.get('MACD_hist', 0) > row.get('MACD_hist', 0):
```

The second condition `row.get('MACD_hist', 0) > row.get('MACD_hist', 0)` is always `False` (a value cannot be greater than itself). This is dead code — presumably intended to compare current MACD hist to previous. The actual comparison that works is only `MACD_hist > 0`.

---

## 🟡 Major Issues (Reliability / Correctness)

### 8. **NS-3 Tier API calls repeat computation 3x**

`/api/v1/tier1` calls `run_tier1()`.  
`/api/v1/tier2` calls `run_tier1()` + `run_tier2(tier1)`.  
`/api/v1/tier3` calls `run_tier1()` + `run_tier2(tier1)` + `run_tier3(tier2)`.  

Each of these re-downloads 52 weeks of ETF data from yfinance. A dashboard that loads all three tiers hits yfinance 5 times for the same data (2× Tier1, 2× Tier2, 1× Tier3). Cache the Tier1 result within the request lifecycle.

### 9. **NS-3 `ytd_return()` — Year detection is locale-dependent**

Line 219:
```python
this_year = prices[prices.index.year == datetime.date.today().year]
```

If `prices` has timezone-aware index and `datetime.date.today()` is naive, the comparison silently returns an empty DataFrame. The function returns `0.0` for all sectors — YTD appears flat when it shouldn't. This is a silent data corruption.

### 10. **NS-3 `compute_adx()` — Incorrect DM filtering**

Lines 160-161:
```python
dm_plus = dm_plus.where(dm_plus > dm_minus, 0)
dm_minus = dm_minus.where(dm_minus > dm_plus, 0)
```

Standard ADX uses Wilder's smoothing, not EMA. And the DM+ should be zeroed when DM+ ≤ DM- (not just `<`). The `.where()` with `> 0` is correct for the direction comparison but the EMA-based smoothing (line 162) diverges from the canonical Wilder's ADX. For quant signals this is an acceptable approximation, but it should be documented as non-standard.

### 11. **Alpha Terminal `serve_file()` — Regex-less HTML injection error-prone**

Lines 290-309: The `serve_file` method searches for `<div class="header">` and `<div class="nav"` with literal string matching, then manually counts tags to find injection points. If the HTML structure changes even slightly (e.g., a `<div>` inside the header div), the injection breaks silently. Should use a proper templating engine (Jinja2) or at minimum DOM parsing.

### 12. **Alpha Terminal `handle_screen()` — Fetches 8 expirations synchronously**

Lines 373-385: For each expiration, calls `get_options_chain()` which triggers a yfinance API call. With 8 expirations × 2 types × ~50 strikes, this is 800 API calls in a single request. No timeout, no pagination, no streaming. A single screen request blocks the single-threaded HTTP server for 30+ seconds.

---

## 🟡 Major Issues (Architecture / Design)

### 13. **Three indicator implementations diverge**

| Indicator | NS-3 `main.py` | NS-4 `main.py` | Alpha Terminal `indicators.py` |
|-----------|----------------|----------------|-------------------------------|
| RSI | `compute_rsi()` (EMA smoothing) | Inline in `calculate_features()` | `calculate_rsi()` |
| MACD | `compute_macd()` (12/26/9) | Inline in `calculate_features()` | `calculate_macd()` |
| ADX | `compute_adx()` (EMA-based) | Inline in `calculate_features()` | Not implemented |
| OBV | `compute_obv()` | Not implemented | Not implemented |

Each uses slightly different parameters and calculation methods. If the Alpha Terminal RSI says 65 and NS-3 RSI says 62 for the same ticker, which is "correct"? This is a reconciliation nightmare.

### 14. **No API versioning strategy**

- NS-3: `/api/v1/tier1`, `/api/v1/tier2`, etc.
- NS-4: `/api/v1/ratios`, `/api/v1/ratio/{symbol}`
- Alpha Terminal: `/api/quotes`, `/api/health` (no version)
- Portal: `/api/strategies` (no version)

When the schemas change (e.g., Tier 1 adds a `riskScore` field), there's no `/api/v2/tier1` — all consumers break at once. The `v1` in NS-3/NS-4 is aspirational.

### 15. **NS-3 QA server copies routes from PROD — but QA runs a different process**

`qa_server.py` line 24: `for route in prod_main.app.routes: app.routes.append(route)`

This copies route **objects**, not route **definitions**. If the PROD `main.py` module changes between QA server restarts, the copied routes become stale. More critically, importing `main` at QA server startup triggers all module-level code — including the `app` global with all middleware. Two separate `FastAPI()` instances exist: one from PROD (imported), one from QA (created locally).

### 16. **No structured logging — unparseable in production**

- NS-3/NS-4: rely on FastAPI/uvicorn default logging (access logs only)
- Alpha Terminal QA: uses `logging` with RotatingFileHandler (good)
- Alpha Terminal PROD (`terminal/server.py`): No logging setup visible

In production, when Tier 3 returns empty, there's no structured error to query. The `except Exception: pass` on line 548 of NS-3 would never be found.

---

## 🟢 Code Quality Issues

### 17. **`warnings.filterwarnings("ignore")` suppresses ALL warnings globally**

Both NS-3 and NS-4 start with `warnings.filterwarnings("ignore")`. This masks deprecation warnings from yfinance, pandas, numpy, and scipy. When a yfinance API changes, you won't get the deprecation warning — the code just silently breaks one day.

**Fix:** Use `warnings.filterwarnings("ignore", category=FutureWarning)` or better, use a context manager for specific operations.

### 18. **`ns_backtester.py` patterns — `*_v2.py`, `modify_*.py`, `patch_*.py`, `revert_*.py`**

The file naming convention suggests a "copy-paste-modify" workflow rather than version control or feature flags:
```
modify_backtester.py
modify_backtester_mom.py
modify_backtester_v2.py
patch_backtester_options.py
restore_rsi.py
restore_rsi_80.py
revert_mom.py
revert_rsi.py
```

This is mutation by accretion. Each file is a snapshot of state at a point in time with no clear lineage. Use git branches for experiments; the main branch should only have the canonical version.

### 19. **Alpha Terminal `handle_quotes()` has a lazy import inside the hot path**

Line 350: `from quotes import get_quotes`

This import fires on every quote request. While Python caches module imports, it still does a dictionary lookup. For a module already imported indirectly (server.py imports config which imports...), this is just unnecessary overhead on the hottest endpoint.

### 20. **`ns3_dashboard.html` loads all three tiers on page load with no lazy loading**

The rewritten dashboard calls `loadTier1()`, `loadTier2()`, `loadTier3()` immediately. All three trigger yfinance downloads server-side. For Tier 3, this means downloading 30 individual stocks' historical data at page load even if the user never clicks the Tier 3 tab. Only the visible tab (Tier 1) should load on init.

---

## 📊 Comparison with First Review (Nemo v1.5)

| Area | Nemo Review | DeepSeek Review |
|------|-------------|-----------------|
| Venv in repo | ✅ Identified | Agreed — critical |
| Duplicate dashboards | ✅ Identified | Agreed — `NS_QA/` is dead code |
| Common lib needed | ✅ Identified | Strongly agreed — found 3 divergent RSI implementations |
| QA/PROD drift | ✅ Identified | Found deeper issue: route object copying, not just config |
| Code logic bugs | Not covered | **New:** Stale holdings, dead MACD comparison, silent except-pass |
| Indicator divergences | Not deep | **New:** Documented all 3 implementations, parameters differ |
| Performance | General caching | **New:** Tier 1 recomputed 5× per dashboard load, screen blocks for 30s |
| Backtester scripts | Seen as clutter | **New:** Core strategy code with zero tests, no docs, no logging |
| API versioning | Not mentioned | **New:** Inconsistent across projects, no v2 strategy |

---

## 🎯 Immediate Action Items (Before Any Refactor)

| Priority | Action | Why |
|----------|--------|-----|
| **P0** | Fix NS-4 `generate_signal()` dead comparison (line 135) | Produces wrong signals silently |
| **P0** | Add logging to all `except Exception: pass` blocks | Silent failures in prod are unacceptable |
| **P0** | Verify `hmmlearn` is installed in PROD Python env | NS-3 may crash on first prod deploy |
| **P1** | Add TTL cache to NS-3 tier computation (5min) | Dashboard hits yfinance 5× per refresh |
| **P1** | Replace hardcoded ETF_HOLDINGS with live yfinance fetch | Tier 3 is running on stale data |
| **P1** | Fix NS-3 `ytd_return()` timezone handling | May show wrong YTD numbers |
| **P2** | Add request-level cache to avoid Tier1→Tier2→Tier3 recomputation | Redundant yfinance calls |
| **P2** | Add `.env`/`config.yaml` for all ports | No more hunting through files |
| **P2** | Delete `NS_QA/`, all `*_v2.py`, `modify_*.py`, `revert_*.py`, `restore_*.py` | 20+ obsolete files |
| **P3** | Extract `indicators/` shared library | 3 divergent RSI impls → 1 |
| **P3** | Standardize API paths (`/api/v1/...`) across all projects | Consistent contract |

---

## 🔧 Proposed Architecture (Longer Term)

```
alpha-terminal/          # Repo 1
├── api/
│   ├── v1/routes/       # FastAPI
│   ├── v2/routes/
├── dashboard/
├── services/            # quotes, financials, options
└── tests/

nine-street/             # Repo 2
├── common/
│   ├── data/yahoo.py    # Shared Yahoo client
│   ├── indicators/      # RSI, MACD, ADX, OBV, HMM
│   └── config.py
├── ns3/
│   ├── tier1.py         # Sector rotation (imports common.indicators)
│   ├── tier2.py
│   ├── tier3.py
│   └── api/
├── ns4/
│   ├── ratios.py
│   └── api/
├── portal/
└── tests/

alpha-quant-common/      # Repo 3 — pip-installable package
├── data/
├── indicators/
├── risk/
└── utils/
```

---

## Summary

The code has solid algorithmic foundations (the 3-tier system, HMM regime detection, Piotroski scoring are all well-conceived) but the engineering is brittle:

1. **Data integrity:** Stale holdings, timezone bugs, dead signal logic
2. **Observability:** Silent exception swallowing, no structured logging, no metrics
3. **Consistency:** 3 indicator implementations with different params
4. **Performance:** Unbounded caching, redundant yfinance calls, sequential ratio screens

The highest-value next step is **not** a repo split — it's auditing all `except: pass` and `except Exception: pass` blocks for correctness, and adding a shared `indicators/` library so that all three systems agree on what RSI(14) means.

---

*Generated by DeepSeek v4 Pro — independent second review*