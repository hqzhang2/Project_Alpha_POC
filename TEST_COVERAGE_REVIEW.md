# Unit Test Coverage Review — Alpha Terminal & Nine Street

> **Status:** Assessment only. No code was changed or added.
> **Date:** 2026-07-22
> **Method:** Ran existing suites on system Python 3.9 with a clean environment
> (`env -u PYTHONPATH` to avoid the known urllib3 pollution). Measured coverage
> with `pytest --cov`. Inventory of source vs. test files across
> `Project_Sequoia/QA_terminal`, `Project_Sequoia/terminal`,
> `Project_Nine_Street/*`, and `common/`.

---

## Headline numbers

| Area | Source modules | Test files | Suite status | Coverage |
|------|---------------|-----------|--------------|----------|
| **Alpha Terminal (QA_terminal)** | 26 `.py` | 14 `test_*.py` | **57 passed** | **56%** (measured) |
| Alpha Terminal (`terminal/`) | 24 `.py` | 7 `test_*.py` | not run — **TBD: is this the canonical tree or a stale mirror of `QA_terminal/`? Owner decision needed** | unmeasured |
| **Nine Street (NS_1/NS-2/NS-3/NS-4 QA servers)** | 4 servers, ~1,560 LOC | **0** | — | **0%** |
| **`common/` shared library** | 7 `.py` (indicators, risk, data) | **0** | — | **0%** |
| Portal (`portal.py`) | 1 | `test_portal_qa.py` | 13 passed | 93% |

---

## Deficiencies (prioritized)

### P0 — Critical gaps (untested code that runs in production)

1. **Nine Street QA servers have zero tests.**
   `NS_1_QA/server_qa.py` (252 LOC, 9 routes: `/`, `/health`, `/api/chart`,
   `/api/nsae`, `/api/nsoe`, `/api/backtest`, `/api/portfolio`,
   `/api/live_feed`), `NS-2_QA/qa_server.py` (827 LOC, 4 routes: `/health`,
   `/api/macro`, `/api/ticker`, `/api/run_all` — the largest NS server),
   `NS-3_QA/qa_server.py` (289 LOC), `NS-4_QA/qa_server.py` (194 LOC).
   These are the actual strategy dashboards behind the portal tabs, totaling
   ~1,560 LOC of untested production-adjacent code. A regression here =
   blank tabs (the failure mode seen at the start of this work).

   **ML testing challenge**: NS-2 uses a 3-state HMM ensemble (5 models,
   seed-dependent convergence; `HMM_STATES=3`, `HMM_ENSEMBLE_N=5`). Unit
   tests must mock the HMM fit to return canned regimes — otherwise tests
   are non-deterministic (different random seeds produce different state
   labels).

   **No HTTP-handler, route, engine-integration, or core-strategy coverage
   whatsoever.** The `generate_signals_v2` function — the multi-factor
   signal generation core, **currently defined only in `NS-2_QA/qa_server.py`
   (candidate core logic, not yet shared with NS-1/NS-3/NS-4)** — is
   exercised only by manual API calls.

2. **`common/` shared library has zero tests** but is imported everywhere
   (`indicators`: `rsi`/`macd`/`sma`/`ema`; `risk`: 10 functions —
   `sharpe_ratio`, `sortino_ratio`, `max_drawdown`, `volatility`, `beta`,
   `alpha`, `var`, `cvar`, `risk_parity_weights`, `equal_weight_returns`;
   `data/yahoo`: caching / rate-limiting / retry). Highest-leverage gap — one
   bug propagates to every project. Note: `from indicators import
   calculate_rsi` currently **fails** (function is named `rsi`), so the import
   contract is already drifting from consumers.

3. **`server.py` (Alpha Terminal) only 60% covered** — and the *uncovered 40%
   is the HTTP handler layer* (routes, error paths, CORS, env handling at
   lines 231–262, 424–447, 497–554, 573–588). This is the layer that produced
   the `PYTHONPATH` 500 during this work. Happy-path routes are tested;
   failure/edge paths are not.

### P1 — Zero-coverage or near-zero modules (Alpha Terminal)

4. **`prediction.py` 0%**, **`greeks.py` 18%**, **`estimates.py` 0%** —
   entirely untested despite being live endpoints (`/api/prediction`, options
   greeks, earnings estimates).

5. **`options.py` 50%**, **`quotes.py` 58%**, **`financials.py` 76%** —
   partial; error branches and fallbacks (`yahoo` fallback in `financials.py`
   lines 176–226) untested. The `sec_financials.py` module that caused the 500
   has **no direct unit test** — only the integration path was exercised
   manually.

### P2 — Test quality / hygiene

6. **`test_endpoints.py` is empty (0 bytes)** — placeholder, gives false
   sense of coverage.

7. **`ns_backtester.py.test` is not a real test** (`import pandas as pd`
   only) — misnamed, not collected by pytest.

8. **Two parallel Alpha Terminal trees** (`QA_terminal/` and `terminal/`)
   with duplicated test files (`test_financials.py`, `test_integration.py`,
   etc. in both). Ambiguity about which is canonical; risk of tests passing
   against the wrong tree.

9. **No coverage gate in CI** — `pyproject.toml` declares `pytest-cov` but
   there is no `--cov-fail-under` or coverage threshold, so regressions like
   the 0% modules are invisible.

10. **No tests for the `PYTHONPATH` guard** added to `server.py` (lines 24–33)
    — the exact fix that resolved this work's incident has zero automated
    protection against reintroduction.

---

## Proposed improvements (for review — not implemented)

1. **Establish a `common/` test suite first** (highest leverage): unit-test
   every `indicators` + `risk` function with known inputs/expected outputs
   (e.g., `sharpe_ratio` on a fixed return series; `rsi` against a Wilder
   reference; `max_drawdown` on a known peak/trough). Fix the `calculate_rsi`
   vs `rsi` import-name drift.

2. **Add NS server tests** mirroring the `portal_qa` pattern: spin each
   `server_qa.py` / `qa_server.py` on an ephemeral port, assert every route
   (`/`, `/health`, `/api/*`) returns correct status + CORS, and that the
   strategy engines degrade gracefully when optional imports are missing.
   For NS-2, mock the HMM fit to return fixed regimes for determinism.
   Note the per-server primary data route differs — NS-1: `/api/chart`,
   NS-2: `/api/ticker`, NS-3/NS-4: their respective primary `/api/*` route
   (verify against each server's `do_GET`).

3. **Raise `server.py` handler coverage**: add tests for the `PYTHONPATH`
   guard, CORS header, 404 path, and each `handle_*` error branch (mock
   `sec_financials` / `yahoo` to force exceptions).

4. **Fill the zero-coverage modules**: `prediction`, `greeks`, `estimates` —
   at minimum smoke tests that the endpoint returns 200 / valid shape.

5. **Integration smoke — Track 0** (see sequence below): one test file per
   NS server that starts the server on an ephemeral port, then `GET /health`
   and `GET` the server's primary data route (NS-1: `/api/chart`,
   NS-2: `/api/ticker`, NS-3/NS-4: their primary `/api/*` route). Catches
   the blank-tab failure mode (HTTP 500 / data pipeline) with minimal
   upkeep. 3 assertions per file, ~1 hour total across all 4 NS servers.

6. **Hygiene**: delete the empty `test_endpoints.py` and the bogus
   `ns_backtester.py.test`; reconcile the dual `QA_terminal` / `terminal`
   trees (pick one canonical, move the other to archive); add a CI coverage
   gate with a phased threshold (start at `--cov-fail-under=60` per the
   SDLC minimum, then ratchet toward 70+ as coverage improves).

7. **CI alignment**: the SDLC mandates ≥80% core / ≥60% overall — current
   measured 56% overall on Alpha Terminal means the gate, if enforced, would
   fail today. The NS and `common` areas are the biggest drags. A
   `--cov-fail-under=70` target upfront would require ~14 points of gain
   before the gate turns green; `--cov-fail-under=60` is more realistic
   as the initial threshold with quarterly ratchets toward 70.
   *"Core" = the high-blast-radius modules that must hit 80%: `common/risk`,
   `common/indicators`, `common/data/yahoo`, `server.py` (Alpha Terminal
   handler layer), and NS `generate_signals_v2`.*

8. **Core strategy logic**: add targeted unit tests for `generate_signals_v2`
   — multi-factor signal generation, the regime state machine, confidence
   sizing, and VIX overlay logic. These are the crown jewels of each NS
   server and currently have zero automated protection.

---

## Recommended sequence

```
Track 0    NS integration smoke tests     4 files, ~3 assertions each, ~1 hour
  ↓
Phase 1    common/ unit tests             highest blast radius, fix import drift
  ↓
Phase 2    NS server unit tests           include NS-2, mock HMM for determinism
  ↓
Phase 3    server.py handler coverage     guard the PYTHONPATH fix, CORS, error paths
  ↓
Phase 4    Zero-coverage modules          prediction, greeks, estimates
  ↓
Phase 5    generate_signals_v2            core strategy logic tests
  ↓
Phase 6    Hygiene + CI gate              delete dead files, set --cov-fail-under=60
```

**Why Track 0 first**: NS servers are what the user sees in the portal tabs.
The blank-tab failure mode (HTTP 500 from a silent data-pipeline exception)
was this session's actual incident. A smoke integration test per server —
start on an ephemeral port, hit `/health` and `/api/ticker` — costs ~15
minutes per server and immediately protects against that regression. It pays
for itself on the first refactor.

---

## Appendix — measured coverage detail (Alpha Terminal `QA_terminal`)

```
config.py       100%
indicators.py   100%
financials.py    76%   (error/fallback branches)
news.py          85%
options.py       50%
quotes.py        58%
server.py        60%   (HTTP handler / error paths uncovered)
estimates.py      0%
greeks.py        18%
prediction.py     0%
TOTAL           56%
```
