# Code Review: Project Alpha POC (v1.5)
**Date:** 2026-07-21  
**Branch:** `release/v1.5` (commit `a3937ed`)  
**Reviewer:** Hermes Agent (Nemotron-3-Ultra)

---

## 📋 Repository Structure

```
/Users/chuck/Project_Alpha_POC/
├── Project_Nine_Street/        # Quant strategies (NS-1, NS-3, NS-4)
│   ├── NS-3_PROD/backend/      # FastAPI algo (port 9206)
│   ├── NS-3_QA/                # QA dashboard + server (port 9237)
│   ├── NS-4_PROD/backend/      # FastAPI ratio trading (port 9210)
│   ├── NS-4_QA/                # QA dashboard + server (port 9241)
│   ├── NS_QA/                  # Legacy QA files (duplicate dashboards)
│   ├── *.py                    # 30+ loose scripts (patches, tests, debug)
│   ├── portal.py / portal_qa.py
├── Project_Sequoia/            # Alpha Terminal (separate project)
│   ├── terminal/               # PROD (port 9098)
│   └── QA_terminal/            # QA (port 9099)
└── *.py                        # Root-level scripts (drift, backtest, etc.)
```

---

## 🔴 Critical Issues

| # | Issue | Impact |
|---|-------|--------|
| **1** | **Venv committed to repo** (`project_alpha_env/`) | 435K+ files tracked — massive bloat, security risk |
| **2** | **30+ loose Python scripts** in `Project_Nine_Street/` root | No organization, unclear purpose, many duplicates (`modify_backtester*.py`, `patch_*.py`) |
| **3** | **Duplicate dashboards** — `NS_QA/` mirrors `NS-3_QA/` & `NS-4_QA/` | Confusion on which is canonical |
| **4** | **NS-3/NS-4 PROD & QA backends diverged** — QA serves static HTML, PROD is FastAPI only | Deployment drift; QA can't test PROD behavior |
| **5** | **No shared library** — yfinance, indicators, config duplicated across both projects | Bugs fixed in one place not propagated |

---

## 🟡 Major Issues

| # | Issue | Recommendation |
|---|-------|----------------|
| **6** | **NS-3 QA server uses different architecture** (uvicorn + static file server) vs PROD (FastAPI only) | Make QA mirror PROD: FastAPI + mount static files |
| **7** | **Port config scattered** — hardcoded in 10+ files | Centralize in single `config.yaml` per project |
| **8** | **NS-3/NS-4 backends have no tests** | Add pytest suite for tier APIs |
| **9** | **Alpha Terminal QA server** is monolithic `server.py` (547 lines) with inline fallbacks | Split into modules: `routes/`, `services/`, `models/` |
| **10** | **No CI/CD pipeline visible** | Add GitHub Actions for lint, test, build |

---

## 🟢 Improvement Proposals

### A. Simplification (Remove Redundancy)

```
DELETE:
├── Project_Nine_Street/NS_QA/              # Entire duplicate dir
├── Project_Nine_Street/patch_*.py          # 6 patch scripts — obsolete
├── Project_Nine_Street/modify_backtester*.py  # 3 versions — keep only latest
├── Project_Nine_Street/revert_*.py         # 2 revert scripts — dead code
├── Project_Nine_Street/restore_*.py        # 2 restore scripts — dead code
├── Project_Nine_Street/fix_*.py            # 3 fix scripts — merge into main
├── Project_Nine_Street/check_win_rate.py   # One-off debug
├── Project_Nine_Street/debug_weights.py    # One-off debug
├── Project_Nine_Street/test_*.py           # 7 test scripts — move to tests/
├── project_alpha_env/                      # Venv — add to .gitignore
└── Root *.py (drift_*, backtest_*, ingest_*, migrate_*, etc.) → move to scripts/
```

### B. Architecture — Extract Common Library

```
Project_Alpha_POC/
├── common/                     # NEW: shared library
│   ├── config.py              # Centralized config (YAML + env)
│   ├── data/                  # yfinance wrapper, caching
│   │   ├── __init__.py
│   │   ├── yahoo.py           # Unified Yahoo Finance client
│   │   └── cache.py           # Redis/disk cache
│   ├── indicators/            # TA library (RSI, MACD, BB, ADX, ATR, HMM)
│   │   ├── __init__.py
│   │   ├── momentum.py
│   │   ├── trend.py
│   │   └── volatility.py
│   ├── risk/                  # Shared risk models
│   └── utils/                 # Logging, health checks, encoding
├── Project_Nine_Street/
│   ├── ns_core/               # NEW: shared NS logic
│   │   ├── tier1.py           # Sector rotation (both NS-3 & NS-4)
│   │   ├── tier2.py           # ETF signals
│   │   └── tier3.py           # Stock selection
│   ├── ns3/
│   │   ├── api/               # FastAPI routes (PROD)
│   │   ├── dashboard/         # HTML/JS (single source)
│   │   └── tests/
│   ├── ns4/
│   │   ├── api/
│   │   ├── dashboard/
│   │   └── tests/
│   └── portal/                # Unified portal (reads common config)
├── Project_Sequoia/
│   ├── alpha_terminal/
│   │   ├── api/               # FastAPI routes
│   │   ├── dashboard/         # HTML/JS
│   │   ├── services/          # quotes, financials, news, options
│   │   └── tests/
└── scripts/                   # One-off scripts (not in root)
```

### C. GitHub Project Separation

| Option | Pros | Cons |
|--------|------|------|
| **Monorepo (current)** | Shared `common/` lib, atomic cross-project changes | Coupled deployments, noisy history |
| **Split: `alpha-terminal` + `nine-street`** | Independent CI/CD, clearer ownership | `common/` must be published as package (PyPI/internal) |
| **Split + Git Submodule for `common/`** | Versioned shared lib | Submodule friction |

**Recommendation:** **Split into two repos** + publish `common` as internal package. Rationale:
- Alpha Terminal (Project Sequoia) and Nine Street are fundamentally different domains
- Different teams, release cadences, scaling needs
- `common` lib versioning prevents "it works in NS-3 but broke Alpha Terminal"

---

## 🔧 Optimization Opportunities

| Area | Current | Optimized |
|------|---------|-----------|
| **Data fetching** | Each service calls yfinance independently | Shared `YahooClient` with rate limiting + cache (Redis/file) |
| **Indicator calc** | Duplicated in NS-3, NS-4, Alpha Terminal | Single `indicators` module, vectorized pandas |
| **NS-3 Tier 1** | Re-fetches 52w weekly data on every request | Cache sector ETF closes for 1h; incremental update |
| **NS-3 Tier 3** | Loops through holdings, calls yfinance per stock | Batch download all holdings in one `yf.download()` |
| **Alpha Terminal quotes** | Sequential per ticker | `yf.Tickers([...])` batch API |
| **Dashboard JS** | Inline in HTML, no bundling | ES modules + Vite build, shared components |
| **Config** | Hardcoded constants | `config.yaml` per env + Pydantic Settings |

---

## 📦 Maintenance — Cross-Reference Map

| Component | Alpha Terminal (Sequoia) | Nine Street (NS-3/NS-4) | Shared? |
|-----------|--------------------------|-------------------------|---------|
| Yahoo Finance | `quotes.py`, `yahoo_financials.py` | `main.py` (inline) | ❌ Duplicated |
| RSI/MACD/BB | `indicators.py` | Inline in NS-3/NS-4 | ❌ Duplicated |
| HMM | Not used | `hmmlearn` in NS-3 | N/A |
| Config | `config.py` (class-based) | Hardcoded constants | ❌ Different |
| Health check | `/api/health` | `/api/v1/health` | Different paths |
| CORS | Manual headers | FastAPI middleware | Inconsistent |
| Logging | `logging` + RotatingFileHandler | `print()` / uvicorn logs | Different |
| Port scheme | 9098/9099 | 92xx series | Different ranges |
| Dashboard | Single HTML + JS modules | 3 separate HTML files | Different |

---

## ✅ Recommended Action Plan

### Phase 1: Cleanup (1-2 days)
1. Add `project_alpha_env/` to `.gitignore`, purge from history (`git filter-repo` or BFG)
2. Delete `NS_QA/`, all `patch_*.py`, `revert_*.py`, `restore_*.py`, `fix_*.py`, debug scripts
3. Consolidate `modify_backtester*.py` → single `backtester.py` in `scripts/`
4. Move root `*.py` scripts → `scripts/`

### Phase 2: Common Library (3-5 days)
1. Create `common/` package with `config`, `data/yahoo.py`, `indicators/`
2. Extract Yahoo client with caching (disk + memory TTL)
3. Extract all TA indicators into `indicators/` (test against current outputs)
4. Add Pydantic Settings for config: `config.yaml` per project

### Phase 3: NS-3/NS-4 Refactor (5-7 days)
1. Make QA servers identical to PROD: FastAPI + `StaticFiles` mount
2. Single dashboard HTML per NS (served by FastAPI)
3. Use `common.data.yahoo` and `common.indicators`
4. Add pytest suite for tier APIs (contract tests)

### Phase 4: Alpha Terminal Refactor (3-5 days)
1. Split `server.py` → `api/routes/`, `services/`, `models/`
2. Use `common.data.yahoo` for quotes/financials
3. Add structured logging + request IDs

### Phase 5: Repo Split (1-2 days)
1. Create `alpha-terminal` repo, move `Project_Sequoia/` 
2. Create `nine-street` repo, move `Project_Nine_Street/`
3. Publish `common` to internal PyPI or GitHub Packages
4. Update CI/CD for both repos

---

## 🎯 Quick Wins (Do First)

| Task | Effort | Impact |
|------|--------|--------|
| Purge venv from git history | 30 min | Repo size ↓ 99% |
| Delete `NS_QA/` duplicates | 5 min | Eliminates confusion |
| Centralize ports in `config.yaml` | 1 hr | Prevents port drift |
| Add `.gitignore` for `__pycache__`, `.env`, `*.log` | 5 min | Hygiene |
| Extract Yahoo client to `common/data/yahoo.py` | 2 hr | Single source of truth |

---

## 📝 Notes

- Current `release/v1.5` branch is at commit `a3937ed` with NS-3/NS-4 QA fixes
- New feature branch `feature/v1.6` created for next development cycle
- Port mapping finalized: QA = PROD + 1 (Alpha 9099/9098, NS-1 9219/9218, NS-3 9237/9236, NS-4 9241/9240)
- Watchlist removed from NS-3 (pure algorithmic system); tier descriptions added

---

*Generated by Hermes Agent code review*