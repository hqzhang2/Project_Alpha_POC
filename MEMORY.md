# MEMORY.md - Chuck's Long-Term Memory

## Email Accounts:
- munger6c@gmail.com (credentials moved to Vault / keychain — do not store passwords in this file)

## Discord Information:
- Hong's User ID: 1467629773663768830
- Primary Channel ID: 1467635682619953164
- Server (Guild) ID: 1467635681948995739
- Strategy Channel ID: 1486569927086313623 (#sequoia-strategy)
- Equity Channel ID: 1486570257312256010 (#sequoia-equity)
- Market Updates Channel ID: 1486570382617215046 (#sequoia-market-updates)
- Trade Ideas Channel ID: 1486570528318951435 (#sequoia-trade-ideas)

## Project Sequoia (Hedge Fund with AI Agents) - DEPRIORITIZED
- **Status:** Deprioritized (as of April 2026). Primary focus shifted to Project Nine Street.
- **Started:** March 1, 2026
- **Role:** COO
- **Initial Planning Doc:** Project_Sequoia/01_Initial_Planning.md
- **Team Doc:** Project_Sequoia/TEAM.md
- **Investment Vehicles:** Equity stocks, index ETFs, commodity ETFs, international ETFs, options (covered calls, puts)
- **Restrictions:** No margin, no futures, no short selling, no naked options
- **Target Return:** 10-20% annualized
- **Max Drawdown:** single digit %
- **Time Constraint:** Max 1 hour/day

## ⚠️ Critical Development Rules (Alpha Terminal):
1. **No changes in Production environment (`Project_Sequoia/terminal/`)** until a formal release branch is cut.
2. **All development work MUST go to the QA environment (`Project_Sequoia/QA_terminal/`).**
3. **Ports are STRICT:** Production is ALWAYS `9098`. QA is ALWAYS `9099`.
4. **Never bypass `deploy.sh`:** Do not manually start servers. The `deploy.sh` scripts set critical `PORT` and `ENV` variables. Missing these will cause QA files to overlap onto the Production port.
5. **QA/PROD file-pair rule (hit 2026-08-17, OMON GEX):** `QA_terminal/` (9099, canonical dev) and `terminal/` (9098, PROD copy) are a **byte-identical file pair**. Make HTML/JS changes in `QA_terminal/` FIRST, verify on QA, THEN sync to `terminal/`. Editing `terminal/` first leaves QA stale → the feature appears "missing on QA but present on prod" (exact OMON GEX bug). Both files must stay byte-identical.

## ✅ SECURITY ISSUE RESOLVED (2026-08-07): Hardcoded API keys in news tab
- **Fixed:** `Project_Sequoia/terminal/news.py` (PROD) — hardcoded Finnhub + NewsAPI fallback keys removed; now env-only (matches QA). Keys moved to BOTH launchd plists (QA + PROD) `EnvironmentVariables`.
- **Also fixed (same file, pre-existing bug):** PROD `news.py` was missing the R2 `ROUTES` dict — news routes were never registered on PROD (404 on every tab). Added; PROD news verified live (100 headlines / 8 economics / 1710 cn items).
- **KEYS ROTATED (2026-08-07, fully closed):** Hong rotated Finnhub (`d9rl99…`) + NewsAPI (`297c1832…`) at the provider dashboards; both plists updated, both services bootout+bootstrap reloaded, old keys grep-verified ABSENT from both processes, live endpoints verified on 9099 + 9098 (headline 100 / M&A 67 / economics 11 / markets 13 / technologies 58). Git-history exposure closed for both providers.
- **MEMORY.md password:** plaintext email password + app password removed from line 4 (2026-08-07) — credentials now Vault/keychain only.

## Sentiment Tab (SHIPPED v2.9.0 2026-08-08)
- **Status:** v2.9.0 deployed to PROD (PR #16, tag v2.9.0). All phases done.
- **Phase 1 DONE (2026-08-07):** skeleton — `sentiment_db.py` (readings + metric_definitions, natural-key upsert, fail-open), `sentiment.py` v2 (14-metric seed, normalization helpers, query layer, ROUTES), `sentiment.html` tab (between 52-Week Lows and News in `header.html`), server.py 3 handlers (`/api/sentiment`, `/api/sentiment/metrics`, `/api/sentiment/providers`). 22 unit tests pass.
- **Phase 2 DONE (2026-08-07):** `sentiment_collect.py` collectors live in QA:
  - `oi_store`: put/call OI ratio per (date, ticker) from own `data/option_oi.db` (capitalized types `'Call'`/`'Put'`!), value=put_oi/call_oi, sentiment=call_share, incremental via `latest_reading_date_for`. 239 rows written.
  - `breadth`: **APPROVED SUBSTITUTION 2026-08-07** — Yahoo delisted ^NYAD/^NAH/^NAL ("Quote not found"); replaced with universe A/D across fixed 39-name `BREADTH_UNIVERSE` (MAG7+large caps+sector ETFs), value=(adv−dec)/(adv+dec) share → passthrough sentiment. Documented proxy in code comment. Live: 08-07 +0.385 (adv=27 dec=12).
- **Phase 3 DONE (2026-08-07):** CBOE + FINRA collectors live:
  - `cboe` (VIX + Equity/Index P/C): VIX from `VIX_History.csv` (real 253d trailing percentile — 08-06: 15.15 → +0.787); P/C from daily page's double-escaped Next.js JSON (`page.replace("\\","")` then regex; plain `Mozilla/5.0` UA — FINRA WAF 403s full Chrome UA). P/C gray until strip history ≥ MIN_PERCENTILE_HISTORY=20.
  - `finra`: discovers latest `shrtYYYYMMDD.csv` from catalog page, pipe-delimited, `daysToCoverQuantity` = parts[9] (revisionFlag is parts[10] — field-index trap), 999.99 sentinel skipped, asof=settlement date, BREADTH_UNIVERSE tickers only. 39 rows (07-15 settlement). Gray until history accrues.
  - **Schema fix (caught live):** SQLite composite PK treats NULL ticker as distinct → market-row duplicates. Fixed with `idx_readings_key` unique index on `(asof_date, scope, COALESCE(ticker,''), metric, source)` + dedup in `init_db()`. Regression tests added. Also: `seed()` now called at import (was missing → definitions table empty).
- **Phase 4 DONE (2026-08-07):** surveys:
  - `naaim`: NAAIM Exposure Index weekly from `index.naaim.org/embeddable/chart` — Symfony UX ChartJS data attr (HTML-escaped JSON `&quot;`, `html.unescape` + `json.loads`), latest point, center_50. Live: 04-29 index 93.79 → +0.876 (their chart data lags ~3mo — their feed, not our bug). No key.
  - `fred`: **REMOVED 2026-08-07 (per Hong)** — UMich `UMCSENT` + OECD composite `USACSCICP02STSAM` were consumer-confidence MACRO data, not market sentiment. Collector + provider + seed rows + live DB rows all removed; `FRED_API_KEY` no longer needed. Restore only if a future macro tab wants it.
  - **AAII LIVE (2026-08-07):** `aaii` provider — weekly bull/bear/neutral via **Firecrawl scrape API** (`api.firecrawl.dev/v1/scrape`, key `FIRECRAWL_API_KEY` in QA plist env). Why: aaii.com is behind **Imperva bot protection** (JS challenge; every scripted HTTP client incl. browser-UA curl gets 403/"Pardon Our Interruption" — only a real browser or Firecrawl passes). Free tier 1,000 credits/mo; AAII needs 1 scrape/week (~4 credits). value = bull−bear spread (pt), sentiment = spread_100. Live: 08-05 spread −1.0pt → −0.01 NEUTRAL. Fallback (option A, unused): weekly Hermes cron with web_extract. Tests 42/42.
  - **View fix (approved):** `latest=1` default view — one row per (scope, ticker, metric, source) via max-asof join (COALESCE-ticker aware), so lagged sources (NAAIM/FINRA/FRED) stay visible. `days=N` remains the explicit history view. Tab has "Latest per metric" checkbox (default on).
- **UI REDESIGN (Hong-approved 2026-08-07, mockup Variant B):** sentiment tab is now **market-only** — per-ticker OI rows moved to the Dashboard as a **P/C overlay**:
  - `sentiment.html`: composite Market-Mood strip (Risk-On/Risk-Off/Mixed from scored-indicator share) + one trend-forward row per market metric (rail: name/value/pill/1W-1M-3M trends/source + Chart.js line), window selector 1M/3M/6M/1Y/ALL. Per-ticker scope UI removed. **Single-point series render a visible dot** (line needs ≥2 pts; history accrues daily — charts fill as collectors run).
  - `dashboard.html`: `renderChart` adds **Put/Call OI** dataset on `y2` axis (RIGHT, orange `#f0883e`, "P/C RATIO" title) — **all timeframes except 1D** (OI is EOD 16:30 snapshot; a 1D line = one flat point). **Ratio is min-max scaled onto the price band so the line OVERLAYS the price line** (one visual line, not two crossing); y2 tick labels map scaled positions back to ratio values (2 decimals, via `_raw` stash on the dataset). Last-Close line REMOVED (per spec). Price stays LEFT. Crosshair + return-of-period + log-scale checkbox kept; **period label removed from return** (was "3M: 7.28%" → "▲ 7.28%").
  - `server.py`: `get_historical_chart` now returns `pc_ratio` (aligned to labels, None where no OI reading) via `ChartDataProcessor._pc_ratio_for()` — reads sentiment store `put_call_oi_ratio` readings, fail-open `[]`.
  - **FRED/UMich/ConfBoard REMOVED (2026-08-07, per Hong):** consumer confidence is macro data, not market sentiment. Dropped from seed (15→13 metrics), collector, provider registry, and live DB (purged 2 def rows). AAII still deferred (WAF-blocked).
  - **AV NEWS_SENTIMENT LIVE (2026-08-07, Phase 5 part 1):** `alphavantage` provider, market-scope aggregate. **AV tickers filter is INTERSECTION-based — multi-ticker calls return 0 articles** (live-verified: SPY alone 50, SPY,QQQ 0) → use the **no-ticker query** (today's top 50 market news, financial_markets/earnings heavy) as the market mood sample. value = mean overall_sentiment_score, count = articles, passthrough. Key `ALPHA_VANTAGE_API_KEY` in QA plist env (added; **restart via `launchctl bootout`+`bootstrap`, NOT kickstart — kickstart doesn't reload plist EnvironmentVariables** — live lesson 2026-08-07). Keyless → providers.alphavantage: False, collector 0. Live: 08-07 v=+0.149 count=50. Sentiment tab now 6 rows incl. News Sentiment. Tests 39/39.
  - Tests: 35/35. Mockups in `sketches/004-007`. Live QA: AAPL 3M overlay shows 4 OI points (08-03→08-06) scaled into price band, 1D has none.
- **Placement:** tab between 52-Week Lows and News. Single nav edit point: `QA_terminal/header.html`.
- **Schema:** `{asof_date, scope, ticker(NULL=market), metric, source, value, sentiment(-1..+1,+1=bullish), count, recorded_at}` + metric_definitions (single interpretation source). 252d trailing percentile; fail-open; keys env-only.
- **COT + MARGIN LIVE (2026-08-07, next-release batch):**
  - `cot`: CFTC **financial futures** report — `fut_fin_txt_YYYY.zip` → `FinFutYY.txt`. **CFTC split equity-index futures out of the regular disaggregated files** (fut_disagg/com_disagg contain NO financial futures — live-verified). Market `E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE`; net spec = `Asset_Mgr` + `Lev_Money` (long−short), count=OI. Weekly, ~3-4d lag. Live: 08-04 +607,034 contracts.
  - `finra_margin`: FINRA margin-statistics page (`/rules-guidance/key-topics/margin-accounts/margin-statistics` — old URL 301s) embeds the monthly table server-side; debit balances, **stored $B** (source $M ÷1000; Jun-26 → 1,502.1 $B). Monthly, ~6wk lag. **Source renamed finra→finra_margin** (distinct natural key from short interest). Live: 06-01 $1,502.1B.
  - Sentiment tab now **9 indicators + 6 composite tiles** (new "Lev & Spec" tile). Tests 46/46.
- **EDGAR + STOCKTWITS LIVE (2026-08-08, dashboard chips + drill-downs, C2 design):**
  - `edgar` collector: SEC submissions API → Form 4s in trailing 90d → per-filing `form4.xml` parse → net buy $M (P buys − S sells; M/F exercises zero-weighted). Value = raw $M; sentiment = percentile over **market-cap-scaled (bps)** history (gray until 20 points accrue). Detail rows → `insider_filings` table for the modal (date/insider/role/code/shares/price/value). **`rptOwnerCik` sits between reportingOwnerId and rptOwnerName in the XML** (regex order trap). Live: AAPL −$15.95M, 10 filings.
  - `stocktwits` collector: stream API needs **browser UA** (python-requests UA → 403); paginate via `cursor.max`. **Sentiment classification is SPARSE (~20% of messages)** — entities.sentiment null on most; spread = (bull−bear)/classified, count = total msgs. Detail rows → `social_daily` per-day. Live: AAPL +0.50 (599 msgs/192 classified, 7d).
  - UI: 2 chips under dashboard ticker (`INS` value+flat/bull/bear, `SOC` spread) → click opens drill-down modal (insider: 3 KPIs + filing table; social: 3 KPIs + daily spread chart + volume table). One-overlay swap, Escape/backdrop close. `fetchTickerSentiment(currentTicker)` in `window.onload` (was missing — chips stayed "—" until watchlist click).
  - API: `/api/sentiment/ticker?ticker=X` → {insider, social} payloads. Metric `social_bull_bear` added (14-seed).
- **ON-DEMAND P/C SNAPSHOT (2026-08-08):** watchlist add fires `/api/oi/snapshot?ticker=X` → `snapshot_oi.snapshot_ticker()` + `sentiment_collect.compute_oi_ratio()` → overlay appears immediately (DBC/VEA/IJH-class tickers were missing — daily universe is a fixed 60-name screener pool). Snapshots date to **last trading day** (`last_trading_day()` via yfinance 5d history) — a Saturday on-demand snapshot dated calendar-today landed AFTER the chart's last bar and stayed invisible. Chart `_pc_ratio_for` now carries readings FORWARD (Friday reading shows on Monday's bar).
- **fix(options) SHIPPED (2026-08-08):** expected-move block did `call_by_strike[atm_strike]` — IJH's asymmetric grid (ATM call 78.0, no matching put) raised KeyError that killed the ENTIRE chain (`error=78.0`); IJH never got chains in OI snapshot or screener. Guard = shared-strike set (`set(call_by_strike) & set(put_by_strike)`).
- **P/C overlay fixes (2026-08-08, Hong):** label 'Put/Call OI' → 'P/C Ratio' (OI is volume, ratio is the signal); tooltip was showing the min-max SCALED price-band value — reads real ratio from aligned `_raw` (`P/C Ratio: 0.50` matches right axis); single-point series render visible dot (pointRadius 4, `ratios.length === 1`).
- **SECURITY CLOSED (2026-08-08):** Finnhub + NewsAPI keys rotated by owner; both plists updated; old keys absent from processes/plists; git-history exposure closed.
- **Run:** launchd `com.alpha.terminal.sentiment.collect` Mon-Fri 16:45 ET (after OI snapshot 16:30) + **`.prod` twin** (same schedule; created at v2.9.0 release); manual: `python sentiment_collect.py` (stdout deliverable).
- **Remaining:** none for sentiment. (Stale `daily_update.py` in terminal/ has a hardcoded `QA_terminal` sys.path — pre-existing, not sentiment-scoped; flag for v3.0.)
- **Rules:** fail-open everywhere; AV/FRED keys only via env; American color (green=bull/red=bear/gray=null); QA → verify → release branch → PROD via deploy_prod.sh.

## OMON Gamma Exposure (GEX) + NS-7 watchdog (SHIPPED v4.2.0 2026-08-17)
**OMON Gamma Exposure chart** — between the option chain table and the IV smile chart, matching a reference GEX layout:
- Computed **client-side from the selected chain** (`OI × gamma × spot × 100`, calls +/puts −). **No date picker** — always reflects the loaded expiry.
- Green call bars / red put bars, blue **Aggregate GEX** cumulative line (right axis), orange **Gamma Flip** line + grey **Last Price** line, **Call Wall / Put Wall** annotations, sign-aware green/red background zones, 6-item HTML legend.
- **Adaptive strike binning (~55 buckets)** via `niceBin` — high-priced tickers (SPY ~$770, strike every $2) otherwise render ~250 thin bars. **±25% moneyness window** around spot keeps the flip/walls meaningful (full chain pulls the flip to a meaningless far-OTM strike). Gamma Flip = interpolated zero-crossing of the cumulative line nearest spot; shading is data-driven (green where cum>0, red where <0).
- Custom Chart.js plugin for the lines/shading/annotations (matches the existing crosshair-plugin pattern; chart.js v4.4.7 has no annotation plugin). Dual y-axis.
- Applied to BOTH `QA_terminal/omon.html` and `terminal/omon.html`, kept **byte-identical** (rule #5 above).
- Verification: live browser render + ad-hoc Node checks (extracted real `computeGex`: math/binning/walls/flip/edge-case; 32/32).

**NS-7 watchdog gap (stale-code incident, fixed):** NS-7 `store.py` was migrated to the centralized Postgres (`common.db`) but `restart_stale_services.sh` did **not** list NS-7, so the QA server ran the pre-migration code and `/health` went red (`sqlite3.OperationalError: no such table` on the empty `data/ns7.db`). **Fix:** added `com.ninestreet.ns7.qa` + `com.ninestreet.ns7.prod` to the watchdog `SERVICES` list with `common_flag=1` (NS-7 store.py imports `common.db`). Lesson: keep the watchdog's `SERVICES` list in sync with which services import `common/` after any migration.

## Human Team Members:
| Role | Name | Focus |
|------|------|-------|
| Lead Investor | Hong | Overall direction |
| Co-investor | TBD | |

## System Events:
- On 2026-02-22, Hong reported a power failure and subsequently set up auto-start for the system.
- On 2026-02-22, Hong requested to defer the data presentation discussion and resume "project alpha poc" tomorrow.
- On 2026-03-18, Completed Sprint 1 (KAN-2, KAN-3, KAN-4, KAN-7 → Done), Created Sprint 2 (KAN-8), Broke down EPICs KAN-5 and KAN-6 into 10 subtasks.
- On 2026-03-18 (PM), Created comprehensive documentation for all 10 subtasks (KAN-9 through KAN-18) in Project_Sequoia/docs/
- On 2026-03-27, Implemented instant followup workflow for sequoia-strategy channel - after Hong posts in strategy/equity/trade-ideas, team automatically responds within minutes
- On 2026-03-31, Released Terminal Alpha v1.3.1: Unified and refactored backend server, added comprehensive full-tab regression testing suite covering Dashboard, OMON, Ratio Analysis, and Financials.
- **2026-07-21:** Restored NS-1/NS-3/NS-4 QA servers with full dashboard APIs. Created release/v1.6 and feature/v1.7 branches.
- **2026-07-23:** 52-Week Highs feature completed in Alpha Terminal QA (feature/v1.8): Market Cap (B) column, finviz ticker cleanup (AADM→ADM etc), OMON-style toolbar + container panel with gradient accent bar, sticky header, sort arrows (▲=asc/▼=desc), zebra striping, removed $1B filter & Reload button. Launchd daily update job at 5pm ET Mon-Fri. All changes in feature/v1.8 branch.
- **2026-07-24:** Release v1.9 cut and deployed to PROD/QA: Alpha Terminal (9098/9099), Nine Street NS-1/2/3/4 (9218-9241/9219-9241), Portal (8000). All PROD plists created and loaded. Calendar icon visibility fixed in 52-Week Highs/Lows with `filter:invert(1)` on native date picker. Calendar button replaced with native dropdown select populated from calendar API. Calendar button bug fixed: changed from `showPicker()` to `.click()` with `type="button"` for reliable cross-browser dropdown. v1.9 released and pushed to feature/v2.0 branch.
- **2026-08-08:** **v2.9.0 released to PROD** (PR #16, owner-approved): sentiment board (9 indicators + 6 tiles) shipped, per-ticker EDGAR insider + StockTwits social (dashboard chips + drill-down modals), on-demand P/C OI snapshot (watchlist add), options.py shared-strike fix, P/C overlay label/tooltip/single-point fixes, Finnhub + NewsAPI keys rotated (security closed). Portal label v2.9.0 | 2026-08-08. PROD sentiment-collect `.prod` launchd twin created. master synced, release/v2.9 + feature/v3.0 pushed (next dev line).
- **2026-08-17:** **v4.2.0 released to PROD** (feature PR #55 + trunk-sync PR #56, owner-approved; master synced to `5c197ec`): OMON **Gamma Exposure chart** + **watchdog NS-7 registration**. Portal label v4.2.0. NO branches deleted (house rule). See "OMON Gamma Exposure + v4.2" below.

## Project Nine Street - Quantitative Trading System
**Status:** ACTIVE PRIMARY PROJECT
**Folder:** `/Users/chuck/Project_Alpha_POC/Project_Nine_Street/`

### Core Tenets:
1. **Persona:** Quantitative Trader. Software is strictly for internal alpha generation and monetization, not for external distribution.
2. **Methodology:** Institutional-grade. Combine PhD-level quant libraries with existing tools to filter false signals and optimize the success ratio.
3. **Objective:** Continuous model improvement focused on maximizing risk-adjusted returns (better returns, less risk).
4. **Strategy:** Creative and adaptive. Emphasize regime change detection, volatility shifts, and balancing predictive modeling with reactive detection.

### 4-Layer Architecture (via claude.md):
- **Layer 1 (Feature Engineering):** Derive TA indicators (ADX, RSI, ATR, BB) and cross-asset signals (credit spreads, VIX) to feed into state models, compressing price/volume history into regime-relevant features.
- **Layer 2 (State Estimation):** Use GMM (Gaussian Mixture Models) for clustering and HMM (Hidden Markov Models) for latent regime detection and transition probabilities.
- **Layer 3 (Signal Generation):** Generate momentum/mean-reversion signals scaled by Layer 2 state confidence (e.g. suppress momentum signals in choppy regimes).
- **Layer 4 (Position Sizing & Execution):** Volatility-target sizing adjusted by regime uncertainty and transaction costs.

## Project Nine Street - Directory Structure & Ports
**Root:** `/Users/chuck/Project_Alpha_POC/Project_Nine_Street/`

### QA Environment (Active):
- `NS_1_QA/` - NS-1 Alpha Engine (server_qa.py + index.html) - Port 9219
- `NS-3_QA/` - NS-3 3-Tier Sector Rotation (qa_server.py) - Port 9237
- `NS-4_QA/` - NS-4 Ratio Trading (qa_server.py) - Port 9241
- `portal_qa.py` - Portal - Port 8000

### PROD Environment:
- `NS_PROD/` - NS-PROD (Next.js frontend + backend)
- `NS-3_PROD/` - NS-3 PROD backend - Port 9236
- `NS-4_PROD/` - NS-4 PROD backend - Port 9240

### Alpha Terminal:
- `Project_Sequoia/QA_terminal/` - QA - Port 9099
- `Project_Sequoia/terminal/` - PROD - Port 9098

**⚠️ RULE #1: Every page/code created MUST be documented in MEMORY.md and committed to GitHub. No memory loss.**

**⚠️ RULE #2: SDLC - Never skip steps.**

## SDLC Process (MUST FOLLOW)
### Development Workflow:
1. Develop in workspace/feature branch
2. Deploy to QA (NOT PROD)
3. User tests/verifies in QA
4. Add unit tests (>60% coverage required)
5. Run regression testing
6. Create release branch
7. Deploy to PROD

### NEVER:
- Deploy directly to PROD without QA
- Skip unit tests
- Skip regression testing
- Skip user verification in QA
- Skip release branch

### QA vs PROD Ports:
| Service | QA | PROD |
|--------|-----|------|
| Alpha Terminal | 9099 | 9098 |
| NS-1 | 9219 | 9218 |
| NS-3 | 9237 | 9236 |
| NS-4 | 9241 | 9240 |
| Portal | 8000 | 8000 |

## Running QA Services (Project Nine Street) - Verified 2026-07-21
| Service | Port | Status | Endpoints |
|---------|-----|--------|-----------|
| Portal | 8000 | ✅ | / |
| Alpha Terminal QA | 9099 | ✅ | /dashboard.html |
| NS-1 QA | 9219 | ✅ | /, /api/nsae, /api/nsoe, /api/backtest, /api/chart, /api/live_feed |
| NS-3 QA | 9237 | ✅ | /, /api/v1/tier1, /api/v1/tier2, /api/v1/tier3, /api/v1/regime, /api/v1/holdings |
| NS-4 QA | 9241 | ✅ | /, /api/v1/all, /api/v1/pairs, /health |

## Project Nine Street - NS-1 (Alpha Engine)
**Status:** ACTIVE - QA restored 2026-07-21
**Folder:** `/Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS_1_QA/`
**Port:** 9219 (QA) / 9218 (PROD)
**Server:** `server_qa.py` (stdlib HTTP)
**Endpoints:**
- `/` - Dashboard (index.html)
- `/api/nsae` - NSAE Feature Engineer signals (20 tickers)
- `/api/nsoe` - NSOE Option Engine (option chains, Greeks)
- `/api/backtest` - Backtest engine (HMM regime, NSAE, SMA)
- `/api/chart` - Chart data with regime overlay
- `/api/live_feed` - Live prices + system events
- `/api/portfolio` - Paper portfolio

## Project Nine Street - NS-3 (3-Tier Sector Rotation)
**Status:** ACTIVE - QA rewritten 2026-07-21
**Folder:** `/Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-3_QA/`
**Port:** 9237 (QA) / 9236 (PROD)
**Server:** `qa_server.py` (stdlib HTTP, no FastAPI deps)
**Endpoints matching dashboard:**
- `/`ns3_dashboard.html`:
  - `/api/v1/tier1` - 11 sectors ranked by momentum vs SPY
  - `/api/v1/tier2` - Top 3 ETF signals (MACD, RSI, ADX, HMM, OBV)
  - `/api/v1/tier3` - Top 10 stocks from qualifying ETFs (RS + Piotroski + TA)
  - `/api/v1/regime` - Market regime (Bull/Neutral/Bear)
  - `/api/v1/holdings?symbol=XLK` - ETF holdings

## Project Nine Street - NS-4 (Ratio Trading)
**Status:** ACTIVE - QA rewritten 2026-07-21
**Folder:** `/Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-4_QA/`
**Port:** 9241 (QA) / 9240 (PROD)
**Server:** `qa_server.py` (stdlib HTTP, no FastAPI deps)
**Endpoints matching `ns4_dashboard.html`:**
- `/api/v1/all` - All 6 pair ratios with indicators + signals
- `/api/v1/pairs` - Same as /all (alias)
- `/health` - Health check
- Pairs: XLK/XLF, XLV/XLY, XLE/XLU, XLI/XLB, XLRE/XLC, SPY/QQQ

## Project Nine Street - Portal
**File:** `Project_Nine_Street/portal_qa.py`
**Port:** 8000
**Tabs:** Alpha Terminal, NS-1, NS-3, NS-4
**Toggle:** PROD/QA switcher updates all iframe URLs dynamically

## Production Deployment Procedure (All Services)

### Overview
All services (Alpha Terminal + NS-1/2/3/4) share a single monorepo and single release branch. One release branch = one version for ALL services. No per-service release branches.

### Directory Structure
```
/Users/chuck/Project_Alpha_POC/
├── Project_Sequoia/
│   ├── QA_terminal/          # Alpha Terminal QA (feature branch)
│   └── terminal/             # Alpha Terminal PROD (release branch)
├── Project_Nine_Street/
│   ├── NS_1_QA/              # NS-1 QA (feature branch)
│   ├── NS-2_QA/              # NS-2 QA (feature branch)
│   ├── NS-2_PROD/            # NS-2 PROD (release branch)
│   ├── NS-3_QA/              # NS-3 QA (feature branch)
│   ├── NS-3_PROD/            # NS-3 PROD (release branch)
│   ├── NS-4_QA/              # NS-4 QA (feature branch)
│   ├── NS-4_PROD/            # NS-4 PROD (release branch)
│   ├── NS_1_QA/              # Legacy NS-1 QA
│   └── ... (shared configs, scripts, docs)
```

### Service Inventory

| Service | QA Dir | PROD Dir | QA Port | PROD Port | Launchd Job (QA) | Launchd Job (PROD) |
|---|---|---|---|---|---|---|
| Alpha Terminal | `Project_Sequoia/QA_terminal` | `Project_Sequoia/terminal` | 9099 | 9098 | `com.ninestreet.alpha.qa` | `com.ninestreet.alpha.prod` |
| NS-1 | `NS_1_QA` / `NS-1_QA` | *(none)* | 9219 | 9218 | `com.ninestreet.ns1.qa` | `com.ninestreet.ns1.prod` |
| NS-2 | `NS-2_QA` | `NS-2_PROD` | 9229 | 9228 | `com.ninestreet.ns2.qa` | `com.ninestreet.ns2.prod` |
| NS-3 | `NS-3_QA` | `NS-3_PROD` | 9237 | 9236 | `com.ninestreet.ns3.qa` | `com.ninestreet.ns3.prod` |
| NS-4 | `NS-4_QA` | `NS-4_PROD` | 9241 | 9240 | `com.ninestreet.ns4.qa` | `com.ninestreet.ns4.prod` |

### Branch Strategy
| Environment | Branch Pattern | Purpose |
|---|---|---|
| QA | `feature/vX.Y` | Development, testing, commits allowed |
| PROD | `release/vX.Y` | Stable, no commits, deployment source |

**Critical rule:** Single release branch = single version for ALL services. No per-service release branches.

### Deployment Procedure
```bash
# 1. Verify current state
git status                          # must be clean
git fetch origin

# 2. Checkout release branch locally
git checkout release/vX.Y           # e.g., release/v2.0

# 3. Deploy to ALL PROD directories
git checkout release/vX.Y -- Project_Sequoia/terminal
# NS-1 PROD (if directory exists)
# git checkout release/vX.Y -- Project_Nine_Street/NS_1_PROD
git checkout release/vX.Y -- Project_Nine_Street/NS-2_PROD
git checkout release/vX.Y -- Project_Nine_Street/NS-3_PROD
git checkout release/vX.Y -- Project_Nine_Street/NS-4_PROD

# 4. Restart ALL PROD launchd services
launchctl kickstart -k gui/$(id -u)/com.ninestreet.alpha.prod
launchctl kickstart -k gui/$(id -u)/com.ninestreet.ns1.prod
launchctl kickstart -k gui/$(id -u)/com.ninestreet.ns2.prod
launchctl kickstart -k gui/$(id -u)/com.ninestreet.ns3.prod
launchctl kickstart -k gui/$(id -u)/com.ninestreet.ns4.prod

# 5. Verify ALL PROD health endpoints
curl -s http://localhost:9098/health   # Alpha Terminal PROD
curl -s http://localhost:9218/health   # NS-1 PROD
curl -s http://localhost:9228/health   # NS-2 PROD
curl -s http://localhost:9236/health   # NS-3 PROD
curl -s http://localhost:9240/health   # NS-4 PROD

# 6. Return to feature branch for continued development
git checkout feature/vX.Y
```

### Automated Deploy Script
```bash
#!/bin/bash
# deploy_prod.sh - Deploy release branch to all PROD dirs
set -euo pipefail

RELEASE="${1:-release/v2.0}"
SERVICES=(
    "Project_Sequoia/terminal"
    "Project_Nine_Street/NS_1_PROD"
    "Project_Nine_Street/NS-2_PROD"
    "Project_Nine_Street/NS-3_PROD"
    "Project_Nine_Street/NS-4_PROD"
)

echo "Deploying $RELEASE to all PROD directories..."
git checkout "$RELEASE" -- "${SERVICES[@]}"

echo "Restarting PROD services..."
launchctl kickstart -k gui/$(id -u)/com.ninestreet.alpha.prod
launchctl kickstart -k gui/$(id -u)/com.ninestreet.ns1.prod
launchctl kickstart -k gui/$(id -u)/com.ninestreet.ns2.prod
launchctl kickstart -k gui/$(id -u)/com.ninestreet.ns3.prod
launchctl kickstart -k gui/$(id -u)/com.ninestreet.ns4.prod

echo "Verifying health..."
sleep 3
for port in 9098 9218 9228 9236 9240; do
    if curl -sf "http://localhost:$port/health" >/dev/null; then
        echo "  Port $port: OK"
    else
        echo "  Port $port: FAILED"
        exit 1
    fi
done
echo "Deployment complete."
```

### Rollback Procedure
```bash
# 1. Identify previous release tag
git tag -l "release/v*" | sort -V | tail -2

# 2. Deploy previous release
./deploy_prod.sh release/vX.Y-1

# 3. Or manually checkout previous release
git checkout release/vX.Y-1 -- "${SERVICES[@]}"
# restart services, verify health
```

### Environment Isolation
- Each environment has separate launchd jobs (see table above)
- Separate log files per environment:
  - QA: `logs/ns2.out.log`
  - PROD: `logs/ns2_prod.out.log`
- Separate working directories (`WorkingDirectory` in plist)
- Separate environment variables (`ENV=QA` vs `ENV=PROD`)

### Monitoring Separation
| Aspect | QA | PROD |
|---|---|---|
| Health endpoint | `http://localhost:9099/health` | `http://localhost:9098/health` |
| Log files | `*_qa.out.log` | `*_prod.out.log` |
| Metrics | Separate dashboards | Separate dashboards |
| Alerting | Dev team | On-call + dev team |

### Post-Deployment Verification
- [ ] All `/health` endpoints return `{"status":"ok"}`
- [ ] NS-2 walk-forward gate status matches expectations
- [ ] Logs show clean startup (no errors in `*_prod.err.log`)
- [ ] Ports listening on correct interfaces
- [ ] Dashboards load at PROD ports
- [ ] No cross-environment contamination

### Fault Recovery
| `feature/v1.6` | main | ✅ Merged (commit 0832717) |
| `release/v1.6` | feature/v1.6 | ✅ Created & pushed |
| `feature/v1.7` | feature/v1.6 | ✅ Created & pushed (current) |
| `feature/v1.8` | feature/v1.7 | ✅ Created & pushed (52-Week Highs feature) |

### Files Changed in v1.6:
- `Project_Nine_Street/NS_1_QA/server_qa.py` (new)
- `Project_Nine_Street/NS_1_QA/index.html` (new)
- `Project_Nine_Street/NS-3_QA/qa_server.py` (rewritten)
- `Project_Nine_Street/NS-4_QA/qa_server.py` (rewritten)
- `Project_Nine_Street/portal_qa.py` (tabs + ports updated)
- `Project_Nine_Street/scripts/nsoe_pricing.py` (vollib import fix)
- `package.json` (new - npm scripts)

## Git Push Policy
**IMPORTANT:** Git pushes should be **postponed until explicitly requested** by the user. Do not auto-push commits unless instructed.

## Session Management
This session has accumulated significant context. For new projects or major feature work, consider starting a fresh Hermes session to avoid context window saturation and ensure clean state. Current session is focused on Project Alpha POC / Nine Street v1.7.

## Portal Health Monitoring (Feature v1.7)
**Location:** `portal_qa.py` — built into the portal frontend
**How it works:**
- Each tab has a **status indicator dot**: ⚪ gray (unknown) → 🟢 green (up) → 🔴 red (down)
- Tabs also get a **green/red left border** for quick visual scan
- Status is checked every **30 seconds** via `fetch()` to each service's `/health` endpoint
- Portal also exposes `/api/health` endpoint returning JSON statuses

**Status check endpoints:**
| Service | Health Check |
|---------|-------------|
| Alpha Terminal | `http://localhost:9099/dashboard.html` |
| NS-1 | `http://localhost:9219/health` |
| NS-3 | `http://localhost:9237/health` |
| NS-4 | `http://localhost:9241/health` |

## Morning Check Routine
**Purpose:** Verify all services are up before trading day starts.

### Step 1: Open Portal
```bash
open http://localhost:8000/
```
Check all tabs have green dots. Red dots = service down.

### Step 2: Quick API Health Check
```bash
for port in 9099 9219 9237 9241 8000; do
  echo -n "Port $port: "
  curl -s -o /dev/null -w "%{http_code}" http://localhost:$port/ 2>/dev/null || echo "DOWN"
done
```
Expected: 5 × 200 responses.

### Step 3: Restart Any Down Services
```bash
# Alpha Terminal
cd /Users/chuck/Project_Alpha_POC/Project_Sequoia/QA_terminal && PORT=9099 /Library/Developer/CommandLineTools/usr/bin/python3 server.py &

# NS-1
cd /Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS_1_QA && PORT=9219 PYTHONPATH="/Users/chuck/Project_Alpha_POC/Project_Nine_Street/scripts" /Library/Developer/CommandLineTools/usr/bin/python3 server_qa.py &

# NS-3
cd /Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-3_QA && PORT=9237 /Library/Developer/CommandLineTools/usr/bin/python3 qa_server.py &

# NS-4
cd /Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-4_QA && PORT=9241 /Library/Developer/CommandLineTools/usr/bin/python3 qa_server.py &

# Portal
cd /Users/chuck/Project_Alpha_POC/Project_Nine_Street && PORT=8000 /Library/Developer/CommandLineTools/usr/bin/python3 portal_qa.py &
```

### Step 4: Regenerate GitHub Token (if pushing)
```bash
PYTHONPATH="" /Library/Developer/CommandLineTools/usr/bin/python3 \
  ### Fault Recovery
- **NS-3/NS-4 down (FastAPI/pydantic error):** The QA servers use stdlib http.server, no FastAPI needed. Re-run the server script directly.
- **NS-1 engines unavailable:** Engines require numba/vectorbt (numpy<2). Dashboard still loads; engine endpoints return 503 gracefully.
- **Portal blank:** Check iframe URL construction. Portal uses `STRATS[key][env.toLowerCase()]` for port.