# Nine Street / Alpha Terminal — Full Stack Review v2

**v2.0 · August 2026**
**Author: Hermes Agent (frontier model)**
**Supersedes: `full_stack_review.md` (v1) — that file is preserved and NOT modified.**

---

## Table of Contents

1. [The Verdict](#1-the-verdict)
2. [The Physics Constraint](#2-the-physics-constraint)
3. [Current Stack Assessment](#3-current-stack-assessment-august-2026)
4. [What Changed Since v1](#4-what-changed-since-v1)
   - [v1 Recommendation Implementation Tracking](#v1-recommendation-implementation-tracking)
5. [Layer-by-Layer Sufficiency](#5-layer-by-layer-sufficiency)
6. [Enhanced Architecture](#6-enhanced-architecture)
7. [The Three Critical Gaps](#7-the-three-critical-gaps)
8. [The Baseball Mandate](#8-the-baseball-mandate)
9. [Service Inventory](#9-service-inventory-qaprod)
10. [End-to-End Workflow](#10-end-to-end-workflow)
11. [What Each Service Contributes](#11-what-each-service-contributes-to-the-goal)
12. [Data Flow](#12-data-flow-v2)
13. [Recommendations](#13-recommendations)
14. [What We Are NOT Building](#14-what-we-are-not-building)
15. [Acceptance Gates (Revised)](#15-acceptance-gates-revised)
16. [Key Design Decisions](#16-key-design-decisions)
[A. Gap-to-Recommendation Mapping](#appendix-a-gap-to-recommendation-mapping)
[B. Service Impact](#appendix-b-service-impact)
[C. Evidence Documents Referenced](#appendix-c-evidence-documents-referenced)

---

## 1. The Verdict

**The toolset is architecturally complete but operationally hollow, and its
return engine is pointed in the wrong direction.**

Three things are true simultaneously:

1. **The drawdown engine works.** GMV weighting + NS-6 achieves 0.20× SPY's
   drawdown, halved in 10 of 11 years. The capital-preservation machinery is
   real and validated.
2. **No configuration beats SPY on return.** The best is 156% (equal-weight)
   vs SPY's 314%. The drawdown engine is not the failure — the **selection
   layer** is.
3. **The live system runs on mocked data.** NS-6's enforcement loop reads
   `current_drawdown = 0.0` because no price pipeline feeds it. The drawdown
   engine, which exists to know how much budget remains, does not know your
   portfolio is down.

In one sentence: **we built a world-class drawdown-protection engine and a
decision cockpit, and pointed them at a value-tilted selection layer, then
never wired live data into either of them.**

---

## 2. The Physics Constraint

The single most important finding since v1, established across four
independent experiments (`structural_tension.md`, `growth_basket_evidence.md`,
`selection_construction_evidence.md`, `regime_conditional_evidence.md`):

> **"Beat SPY" and "halve drawdown" are the same knob, not two.**

Return and drawdown both come from the same beta. The evidence:

| Portfolio | Ann. return | Sharpe | Max DD |
|---|---|---|---|
| MAG7 growth basket + fast de-risk | 27.3% | **1.33** | −41.9% |
| QQQ + fast de-risk (+30% floor) | 15.0% | 0.98 | −32.4% |
| SPY buy&hold | 15.2% | 0.83 | −33.7% |
| GMV (min-variance) + NS-6 | ~4.7% | 0.73 | **0.22×** (−7.5%) |

- The **growth factor** (MAG7 basket, Sharpe 1.33) is the return engine, but
  it carries −42% drawdown — *worse* than SPY. Growth names correlate ~0.58
  off-diagonal and crash together ("fake diversification": diversifying *within*
  the growth factor does nothing for drawdown).
- The **defensive factor** (GMV) delivers 0.22× drawdown but 0.73 Sharpe.
- There is **no long-only, no-leverage portfolio at high return AND low
  drawdown simultaneously.** They are one axis.

**Implication:** the mandate "match SPY with half the drawdown" is not an
engineering problem with a clean solution — it is a *point on a frontier*.
The value of the stack is that we can now **map and execute** that frontier
precisely, and let the PM pick the point. That is the correct frame.

---

## 3. Current Stack Assessment (August 2026)

### What Exists

| # | Service | Port (QA/PROD) | Purpose | Status |
|---|---|---|---|---|
| — | **Alpha Terminal** | 9099/9098 | Market data, fundamentals, screening, macro, sentiment, options | ✅ Live |
| — | **NS-1** | 9219/9218 | Capital preservation ETF rotation (VIX smile curve) | ✅ Live |
| — | **NS-2** | 9229/9228 | HMM regime detection per ticker (MAG7 + watchlist) | ✅ Live |
| — | **NS-3** | 9237/9236 | Sector rotation (cross-sectional ranking vs SPY) | ✅ Live |
| — | **NS-4** | 9241/9240 | Deferred placeholder | 🔧 Deferred |
| — | **NS-5** | 9251/9250 | Portfolio governance engine (concentration, drift, tax, regime) | ✅ Live |
| — | **NS-6** | 9261/9260 | Drawdown engine & scenario cockpit | ✅ Live (Phase 1) |
| — | **NS-7** | (9271/9270) | Growth/momentum selection service | 🛠️ Designed, not built |
| — | **Portal** | 8000 | Unified iframe dashboard for all services | ✅ Live |

### Alpha Terminal — Detailed Capabilities

| Component | What It Does | Relevance |
|---|---|---|
| `fundamental_screener.py` | 4-framework ensemble (Graham/Greenblatt/Lynch/Buffett) on ~1,700+ tickers. Point-in-time SEC XBRL data. Agreement ≥2 = sweet spot (+4.85pp/yr vs equal-weight base, walk-forward validated 2016-2026). ADR-ratio adjustment, 2-year staleness guard. | **Stock selection engine — value/quality.** The primary source of which SP500 value stocks to own. **v2 note:** validated against a value-tilted base, not growth-weighted SPY; structurally misses mega-cap growth leaders in a growth decade (see §2, §5 Gap 1). |
| `screener.html` | Interactive screener UI: filter by agreement, framework detail, forward 1-year performance | PM-facing stock research |
| `macro.py` + `macro.html` | FRED macro indicators (GDP, CPI, UNRATE, yield curve, VIX, PMI). Regime sub-tab (7th subtab): R1-R4 regime detection, 6 gauges, 24-month calendar. | Regime context for PM decisions. Feeds NS-5 regime axis + NS-6 regime → profile suggestion. |
| `estimates.py` + `estimates.html` | Earnings estimates: consensus, revisions, trends, surprise history | Supplementary: earnings momentum |
| `financials.py` + `financials.html` | 4-tile financial statements (income, balance sheet, cash flow, ratios) | Deep-dive research |
| `sentiment.py` + `sentiment_collect.py` | Market sentiment: CBOE put/call, FINRA short data, VIX, NAAIM, StockTwits | Market-fear gauges |
| `ratio.html` + `indicators.py` | ETF-vs-SPY ratio analysis with technical indicators. Cross-sectional ranking. | Sector/ETF relative strength |
| `options_data.py` / `options.py` | Options chain data (Polygon-backed), Greeks | **Pricing data for puts and covered calls in NS-6** |
| `year_highs.py` / `year_lows.py` | 52-week high/low screens | Supplementary scanning |
| `sp500_history.py` | Survivorship-aware SP500 universe construction | **Critical for backtest accuracy.** Consumed by NS-7 for survivorship-aware momentum. |
| `fundamentals_history.py` | Point-in-time fundamentals store (SQLite) | Data backbone for screener |

### NS Services — Detailed Capabilities

| Service | What It Does | Relevance |
|---|---|---|
| **NS-1** CP | VIX smile curve ETF rotation: VIX > 28 → safe havens (TLT, GLD, BIL, AGG). Monthly rebalancing, BIL as cash equivalent. Two profiles (conservative/aggressive). Max DD improved −21.7% → −18.9% over 16yr. | **Precedent for drawdown protection.** Binary model. ETF-only. The VIX-smile pattern directly informed NS-6's fast de-risk design (see §2). |
| **NS-2** HMM | 3-state HMM per ticker (TRENDING/MEAN_REV/CRISIS). Walk-forward OOS, acceptance gates (PASS/MARGINAL/NO-EDGE). Asset-class profiles. Momentum short-ban. | **\"Does this stock still have gas?\"** Gating signal for drift alerts and NS-7 per-ticker regime gate. Ticker-level regime context. |
| **NS-3** Rotation | 11-39 pairs ranked by relative momentum vs SPY. Top-3 basket crosses to Tier 2 (absolute gate). Walk-forward: Tier 1 at base rate, mom12 best, absolute gate destroys value. | **Sector-level ranking.** Supplementary context for ETF selection. |
| **NS-4** | Deferred placeholder. | Not relevant. |
| **NS-5** Governance | 4-axis grading (concentration, drift, tax, regime). Efficient frontier (Ledoit-Wolf + SLSQP long-only). Factor model (MKT/SMB/HML/MOM/DUR). Portfolio composite grade = base × regime enhancer. Tax axis: after-tax frontier, TLH, asset location, basis erosion. Drift axis: weight/risk/style/frontier drift. Regime axis: GDP×CPI 2×2 + 5 confirmation layers. Radar dashboard with 12 sub-axes. | **Governance backbone.** Provides target weights, risk grades, tax data, regime state, drift measurements. Consumed by NS-6. **v2 gap:** frontier output is computed but not persisted as a file NS-6 can read (R2b). |
| **NS-6** Drawdown | Budget tracking, graduated exposure enforcement, scenario engine (add/remove/replace), drift alerts with NS-2 regime context, fast de-risk (v2 validated, not wired), protective put overlay, covered call gate, circuit breakers (stubbed), position stops (stubbed), PM cockpit with three switchable profiles + portfolio source selector + drift inbox + scenario cockpit + enforcement dashboard. | **Drawdown engine + PM cockpit.** Tier 1-4 fully built, deployed QA + PROD. **v2 gaps:** enforcement reads `current_dd=0.0` (no price pipeline — R2a); drift compares vs hand-tuned `DEFAULT_WEIGHTS` (no NS-5 frontier feed — R2b); scenario runs blind (no screener feed — R2c); fast de-risk validated but not wired into live loop (R3). |
| **NS-7** Growth/Momentum | (Designed, not built.) Skip-month momentum (126/21) over SP500 + $50B+ universe, two-league qualifier (major/minor, 90d grace), quality floor veto, concentration caps. Output feeds NS-5 frontier (growth sleeve) → NS-6 drawdown. 18 core-logic tests passing. | **Return engine.** Addresses Gap 1 (no growth selection). Designed in `NS-7_QA/` (DESIGN.md, config.py, universe.py, selector.py, store.py, tests/). Specifies the R1 recommendation. |

### Scripts Layer (Not Live Services)

| Script | Purpose | Relevance |
|---|---|---|
| `ns_backtester.py` | vectorbt-based backtesting with HMM regime, SMA cross, NSAE strategies | Harness pattern for NS-6 backtest |
| `ns_monte_carlo.py` | Bootstrap MC simulation | Supplementary risk projection |
| `ns_quant_models.py` | GMMHMM regime detection, cointegration | HMM pattern, but NS-2 supersedes for live use |
| `ns_capital_preservation.py` | CP strategy backtest | NS-1's progenitor |
| `ns_layer4_execution.py` | Options overlay simulation (covered call proxy) | Pattern for NS-6's options module |

---

## 4. What Changed Since v1

| v1 Item | v1 Status | v2 Status |
|---|---|---|
| NS-6 `budget.py` | To build | ✅ Built + tested |
| NS-6 `enforcement.py` (linear multiplier) | To build | ✅ Built — Phase 1 budget-only **live** |
| NS-6 `enforcement.py` v2 (multi-signal + fast de-risk) | Tier 2 | ✅ Built (`vix_smile_cap`, `fast_derisk_exposure`) — **NOT wired live** |
| NS-6 `rebalance.py` / `scenario.py` / `drift_alert.py` | To build | ✅ Built + tested |
| NS-6 `options.py` / `tax_context.py` | Tier 3 | ✅ Built + tested (proxy-based) |
| NS-6 dashboard + portal | Tier 4 | ✅ Built + deployed (QA 9261, PROD 9260) |
| Three switchable profiles (growth/balanced/CP) | Not in v1 | ✅ Built + deployed |
| Portfolio source (NS-5 selector + model fallback) | Not in v1 | ✅ Built + deployed |
| Regime → profile suggestion | Not in v1 | ✅ Built (real `regime_store` read) |
| **Price ingestion → enforcement** | Tier 1 (the deliverable) | ❌ **Still missing** — `current_dd=0.0` |
| **NS-5 frontier → drift target** | Tier 1 | ❌ **Still missing** — compares vs hand-tuned `DEFAULT_WEIGHTS` |
| **Screener verdict → scenario** | Tier 1 | ❌ **Still missing** — optional param the dashboard never sends |
| **Growth selection methodology** | Not identified | ❌ **Missing** — momentum exists only as an experiment |
| End-to-end walk-forward | Tier 1 (the deliverable) | ✅ Done (but exposed the tension, not a win) |

**Net:** v1's architecture is fully built and deployed. What v1 *didn't*
anticipate is that building it would expose two deeper problems: (a) the
selection layer is value-tilted against the growth decade, and (b) the data
pipeline that makes the engine real was never wired. Both are now the binding
constraints.

### v1 Recommendation Implementation Tracking

What v1's Implementation Priority (§9 of v1) called for, and what actually happened:

| v1 Recommendation | Tier | v1 Verdict | Status | Notes |
|---|---|---|---|---|
| NS-6 `budget.py` | Tier 1 | Foundation | ✅ **Done** | Live in `qa_server.py` enforcement loop |
| NS-6 `enforcement.py` Phase 1 (linear multiplier) | Tier 1 | "First working drawdown protection" | ✅ **Done** | Budget-only multiplier live; **no price ingestion** — reads `current_dd=0.0` |
| NS-6 `rebalance.py` | Tier 1 | Funding path logic | ✅ **Done** | Built; funding-path trade-ticket rendering fixed |
| NS-6 `scenario.py` | Tier 1 | PM cockpit MVP | ✅ **Done** | Add/remove/replace built; **runs blind** — screener scores, prices, NS-2 regimes are optional params the dashboard never sends |
| NS-6 `qa_server.py` + APIs | Tier 1 | Wire everything | ✅ **Done** | 177 tests, deployed QA + PROD |
| `ns6_backtest.py` walk-forward | Tier 1 | "The deliverable" | ✅ **Done** | Walk-forward 2017-2026; **exposed the structural tension**, not a win |
| NS-6 multi-signal multiplier (v2) | Tier 2 | "~100 lines, huge improvement" | ✅ **Built, NOT wired** | `fast_derisk_exposure()` + `vix_smile_cap` implemented and validated (Sharpe 0.98); live loop still calls Phase 1 budget-only |
| NS-6 protective put overlay | Tier 2 | New module | ✅ **Done** (proxy) | `options.py` built; backtest-tested with proxy data; no live option chain ingestion |
| NS-6 `drift_alert.py` | Tier 2 | Advisory output | ✅ **Done** | NS-2 regime gating matrix + urgency; **target = hand-tuned DEFAULT_WEIGHTS**, not NS-5 frontier |
| NS-6 tax-aware funding paths | Tier 3 | After-tax cost per path | ✅ **Done** | `tax_context.py` built; proxy-based |
| NS-6 covered call gate | Tier 3 | Gate logic | ✅ **Done** | Gating logic in enforcement; multiplier check wired |
| NS-6 circuit breakers | Tier 3 | Hard floor, systemic breaker, stops, hysteresis | ⚠️ **Stubbed** | Functions exist; status returns empty `[]`/`None`; not wired to live data |
| NS-6 dashboard | Tier 4 | Full interactive HTML cockpit | ✅ **Done** | Built + deployed (QA + PROD); scenario cockpit, drift inbox, enforcement events, profile switch, portfolio source selector |
| Portal integration | Tier 4 | 4-point wiring pattern | ✅ **Done** | NS-6 tab in portal on both QA and PROD |
| Iron condor on SPY | Tier 4 | Low-exposure income | ❌ **Won't build** | Phase 5 only; de-prioritized by the physics constraint — the stack's problem is return, not income |
| Regime-conditional covariance | Tier 4 | Multi-year research | ❌ **Won't build (v1 scope)** | Research project; not the binding constraint; NS-5's single-period Ledoit-Wolf is sufficient for the current book size |

**Summary:**
- **Implemented:** 12 of 15 v1 recommendations (budget, enforcement, rebalance, scenario, server, backtest, multi-signal, puts, drift, tax, calls, dashboard, portal).
- **Built but not wired:** 4 items (price ingestion → enforcement, NS-5 frontier → drift, screener → scenario, fast de-risk into live loop). These are R2-R3 in v2.
- **Stubbed:** 1 item (circuit breakers). Not the binding constraint — addressed when the data pipeline carries real drawdown values.
- **Won't build:** 2 items (iron condor, regime-conditional covariance). Not the binding constraint; the stack needs growth selection (R1) and data wiring (R2), not more optimization layers.

---

## 5. Layer-by-Layer Sufficiency

Assessment guideline — a layer is "sufficient" only if it answers its core
question **with live data, today**, not with a spec or a mock.

| Layer | Core question | Status | Why |
|---|---|---|---|
| **Selection** | "What do I own?" | 🔴 Insufficient | Value/quality screener (Graham/Greenblatt/Lynch/Buffett) never picks the Mag 7 with high agreement. Picks KDP, TROW, WSM, BLDR — fine businesses, no growth factor. No production growth/momentum selector. |
| **Construction** | "How much of each?" | 🟠 Partial | NS-5 frontier (Ledoit-Wolf + SLSQP) produces sound targets, but NS-6's drift compares against a hand-tuned `DEFAULT_WEIGHTS` list, not the frontier output. |
| **Management** | "What changed, what do I do?" | 🟠 Partial | Scenario + drift engines are built and correct, but run blind — screener verdicts, prices, and NS-2 regimes are optional params the dashboard never sends. |
| **Protection** | "Am I within budget?" | 🔴 Insufficient | Enforcement loop reads `current_dd=0.0`, `spy_dd=0.0`. Circuit breakers, position stops, protective puts all return empty/None. The engine cannot protect a drawdown it cannot see. |
| **Execution** | "Execute." | ✅ Sufficient | Manual by design (PM signs the ticket). Correct — no auto-execution is a feature, not a gap. |

The pattern: **the compute layer (engines, math, gates) is strong; the data
layer feeding it is hollow.** Every critical decision path bottoms out in a
hardcoded value or a silently-empty optional parameter.

---

## 6. Enhanced Architecture

Updated from v1 to reflect NS-6 (built), NS-7 (designed), and the regime-gated
two-sleeve blend (v2's key architectural change vs v1's single-universe frontier).

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          FULL STACK (v2)                                 │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                      DATA LAYER                                   │   │
│  │  SEC XBRL ──► fundamentals_history.py ──► SQLite (point-in-time)  │   │
│  │  yfinance ──► daily closes, fundamentals, estimates               │   │
│  │  FRED    ──► regime_fetcher.py ──► regime_history.db              │   │
│  │  Polygon ──► options_data.py ──► option_oi.db                     │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                    │                                     │
│                                    ▼                                     │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                     SELECTION LAYER         ← v2: TWO SELECTORS   │   │
│  │                                                                    │   │
│  │  ┌─────────────────────┐     ┌─────────────────────┐              │   │
│  │  │ A_T Screener        │     │ NS-7 Momentum       │ ← NEW       │   │
│  │  │ Value/quality       │     │ Gross/Momentum      │              │   │
│  │  │ 4-framework x-ref   │     │ Skip-month 126/21   │              │   │
│  │  │ (defensive sleeve)  │     │ 2-league qualifier  │              │   │
│  │  └─────────┬───────────┘     └─────────┬───────────┘              │   │
│  │            │                           │                           │   │
│  │            ▼                           ▼                           │   │
│  │     value sleeve            growth sleeve                          │   │
│  │  regime-conditional allocation (growth → tilt growth;               │   │
│  │    defensive → tilt value)                                          │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                    │                                     │
│                                    ▼                                     │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                    CONSTRUCTION LAYER                              │   │
│  │                                                                    │   │
│  │  ┌────────────────────────────────────────────────────────────┐  │   │
│  │  │ NS-5 Governance Engine                                      │  │   │
│  │  │  frontier.py     → target weights (Ledoit-Wolf + SLSQP)    │  │   │
│  │  │                    → persisted as frontier.json (R2b)      │  │   │
│  │  │  concentration.py → factor loading, sector, effective N     │  │   │
│  │  │  drift.py        → weight/risk/style/frontier drift        │  │   │
│  │  │  tax.py          → after-tax frontier, TLH, asset location │  │   │
│  │  │  regime.py       → macro regime (R1-R4), enhancer          │  │   │
│  │  │  portfolio_store → holdings, weights, lots, accounts       │  │   │
│  │  └────────────────────────────────────────────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                    │                                     │
│                                    ▼                                     │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                     ENFORCEMENT LAYER        ← NS-6 (built)       │   │
│  │                                                                    │   │
│  │  ┌──────────────────┐  ┌──────────────────┐  ┌───────────────┐  │   │
│  │  │ Scenario Engine  │  │ Drawdown Engine  │  │ Drift Alerts  │  │   │
│  │  │                  │  │                  │  │               │  │   │
│  │  │ Add/remove/repl. │  │ Budget tracker   │  │ Quarterly     │  │   │
│  │  │ Funding paths    │  │ Fast de-risk     │  │ drift check   │  │   │
│  │  │ Tax impact       │  │ (VIX smile+R3)   │  │ NS-2 gated    │  │   │
│  │  │ Price sensitivity│  │ Put overlay      │  │ Advisory      │  │   │
│  │  │ PM-INITIATED     │  │ Circuit breaker  │  │ QUARTERLY     │  │   │
│  │  └──────────────────┘  └──────────────────┘  └───────────────┘  │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                    │                                     │
│                                    ▼                                     │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                      EXECUTION LAYER                               │   │
│  │  Trade Ticket (PM-signed list) + Options Execution (put, CC)       │   │
│  │  MANUAL — PM in the loop by design                                 │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                    SUPPORTING SIGNALS                              │   │
│  │  NS-2 HMM  ──► Per-ticker regime (TRENDING/MEAN_REV/CRISIS)      │   │
│  │  NS-3 Rota ──► Sector relative strength (supplementary)           │   │
│  │  NS-1 CP   ──► VIX-based drawdown precedent (design reference)    │   │
│  │  A_T Macro ──► VIX, yield curve, FRED (daily macro context)       │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

**Key architectural changes from v1:**
1. **Two selectors, not one** — the selection layer now has NS-7 (growth/momentum) alongside the A_T screener (value/quality). They feed two sleeves sized by regime.
2. **NS-6 is no longer "to build"** — it is the enforcement layer, with fast de-risk replacing the slow multiplier (R3), and the data pipeline (R2) connecting it to real prices, frontier targets, and screener verdicts.
3. **NS-5 frontier is now a persisted file** — `frontier.json` so NS-6 can read it without importing NS-5 (R2b, matching the portfolio-source pattern).
4. **Regime-conditional allocation** — the growth:value tilt by regime replaces v1's single-universe frontier as the primary blend mechanism (addressing the proven-dead "B" and "C" paths).

---

## 7. The Three Critical Gaps

Ranked by impact on the mandate.

### Gap 1 — No growth selection methodology (most critical)

The return engine is missing. The evidence is decisive:

- Value screener: **143.9%** vs SPY **314.1%** (full window). Beat SPY in 1/11 years.
- Pure momentum selection: **227.4%** — **+83pp over value** on the *same*
  engine. Still trails SPY but is the only selector that comes close.
- QQQ buy&hold: **+513%** — the growth factor *crushes* SPY, with *higher*
  Sharpe (0.92 vs 0.83).

The screener's +4.85pp/yr edge is real but measured against a **value-tilted
base**, not growth-weighted SPY. The four frameworks (Graham, Greenblatt,
Lynch, Buffett) all share a quality-at-reasonable-price bias that
systematically excludes the expensive, high-growth names that drove the decade.

**The only growth-capable methodology in the codebase — pure momentum — is an
experiment (`selection_construction_experiment.py`), not a service.** NS-2's
TRENDING regime is *timing* (does this stock still have gas?), not *selection*
(which stocks should I own). There is no production "growth screen" that does
for growth what the value screener does for value.

This is the most critical gap because it is the binding constraint on **both**
halves of the mandate: with no growth selector, you can halve drawdown (GMV)
but never match SPY, and the return/drawdown knob is meaningless because you
hold no growth beta to trade.

### Gap 2 — The live data pipeline is unwired

The engine cannot operate on reality:

- **Price ingestion → enforcement** is "Phase 2," never built. The drawdown
  engine reads `current_dd=0.0` — it does not know your portfolio is down, so
  it cannot enforce the floor. This is the core promise of NS-6 and it is not
  live.
- **NS-5 frontier → drift target** is a hand-tuned `DEFAULT_WEIGHTS` magic
  list. The governance engine produces real target weights; NS-6 ignores them.
- **Screener verdict → scenario** is an optional `screener_scores` param the
  dashboard never sends. The PM's "should I add this stock" scenario runs
  without the screener's own verdict.

This is what the user means by "hacks and manual intervention": the pieces
exist but a human must hand-feed them. The pipeline is the connective tissue
between selection/construction (which know things) and enforcement (which must
act on them).

### Gap 3 — Slow quarterly multiplier is the wrong de-risking mechanism

The v2 fast de-risking (`vix_smile_cap` + `fast_derisk_exposure`) is **built
and validated** — Sharpe 0.96–0.98 vs the slow quarterly multiplier's
0.70–0.81 — but the **live** enforcement loop still calls the Phase 1
budget-only `compute_exposure_multiplier()`. The correct mechanism exists in
the code and is not wired in.

This is lower-ranked than the other two because the fix is mechanical (swap
the call), but it is worth naming separately: it is the difference between
NS-6 being a *net-negative* overlay on growth (slow) and a *Sharpe-improving*
one (fast, floored smile).

---

## 8. The Baseball Mandate

The user's frame: **HFT is basketball; we play baseball.** Not many back-and-forth
scores — always ready to hit home runs.

This resolves a tension the v1 gates got wrong. A baseball book is:

- **Low turnover** — 30 trades/month is not just soft, it should be *far*
  under 30. Baseball scores few runs; the constraint that binds is **position
  concentration** (how many home-run swings) and **drawdown budget** (how long
  I can wait in the batter's box before the floor forces me out), not trade
  frequency.
- **High conviction, concentrated** — the current 12-name, ~25-stock book is
  the right shape. Momentum selection is naturally low-turnover, which fits
  the baseball frame better than value's mean-reversion churn.
- **Patient under drawdown** — "don't trim trending winners" (NS-2 gates drift)
  is exactly the baseball discipline. The fast de-risking floor (never go to
  zero) is the mechanical expression of "stay in the box through the crisis."

**Consequence for acceptance gates:** trade count is the wrong metric to
optimize. Replace "avg trades/quarter < 30" with "conviction concentration +
max-drawdown-while-waiting." The home-run book is judged on whether it holds
the growth factor through drawdowns, not on how often it swings.

---

## 9. Service Inventory (QA/PROD)

| # | Service | QA Port | PROD Port | Status | Role in Goal |
|---|---|---|---|---|---|
| — | **A_T** | 9099 | 9098 | ✅ Live | Stock selection (value/quality), ETF data, macro, sentiment, options pricing |
| — | **NS-1** | 9219 | 9218 | ✅ Live | VIX-based drawdown precedent (design reference) |
| — | **NS-2** | 9229 | 9228 | ✅ Live | Per-ticker regime context for drift alerts + NS-7 per-name gate |
| — | **NS-3** | 9237 | 9236 | ✅ Live | Sector-level relative strength (supplementary) |
| — | **NS-4** | 9241 | 9240 | 🔧 Deferred | TBD |
| — | **NS-5** | 9251 | 9250 | ✅ Live | Governance: target weights, risk grades, tax data, regime state, frontier.json (R2b) |
| — | **NS-6** | 9261 | 9260 | ✅ Live (P1) | Drawdown engine, scenario cockpit, drift alerts, PM profiles, portfolio source |
| — | **NS-7** | (9271) | (9270) | 🛠️ Designed | Growth/momentum selection — return engine (R1) |
| — | **Portal** | 8000 | — | ✅ Live | Unified dashboard for all services |

## 10. End-to-End Workflow

### Daily Rhythm (updated for v2)

| Time | Event | Service |
|---|---|---|
| **Continuous** | PM researches, identifies potential adds/removes. Runs scenarios in NS-6 with screener verdicts (R2c). | NS-6 scenario engine + A_T screener |
| **0900 ET** | NS-6 price feed updates (R2a): fetches EOD closes → computes current drawdown + SPY drawdown + VIX level. Enforcement loop runs with fast de-risk (R3). | NS-6 enforcement (launchd cron) |
| **1615 ET** | A_T data collection: EOD prices, sentiment, new SEC filings. | A_T (launchd cron) |
| **1630 ET** | PM reviews: drift alerts (now vs NS-5 frontier — R2b), enforcement status (live drawdown), scenarios to execute tomorrow? | NS-6 dashboard |
| **1645 ET** | PM signs trade ticket. Executes at next market open. | Manual |

### Quarterly Rhythm

| Event | Service |
|---|---|
| Re-run A_T screener for current SP500 universe | A_T |
| Re-run NS-7 momentum selector for growth sleeve | NS-7 (new in v2) |
| Re-run NS-5 frontier for unified universe (value ∪ momentum) | NS-5 |
| Persist frontier.json for NS-6 drift target | NS-5 (R2b) |
| NS-6 quarterly drift check: current vs frontier targets, NS-2 regime-gated | NS-6 |
| PM reviews drift alerts, runs replace scenarios if selector turnover requires | NS-6 |
| PM executes approved changes over the quarter (smooth the trade count) | Manual |

### PM-Initiated Scenario (updated with R2c)

1. PM identifies a new idea (from research, news, NS-2 signal flip, NS-7 momentum change)
2. PM opens NS-6 scenario builder, enters ticker + proposed weight
3. NS-6 dashboard fetches A_T screener verdict for the ticker (R2c)
4. NS-6 returns: screener verdict, NS-5 target, funding paths (ranked), price sensitivity, drawdown impact
5. PM reviews paths, may adjust price/weight and re-run
6. PM decides: execute, wait for better entry, or pass
7. If execute: NS-6 generates trade ticket factoring current drawdown multiplier (now fast de-risk — R3)
8. PM signs and executes

## 11. What Each Service Contributes to the Goal

### Direct Contributions

| Service | "Outperform SPY" | "Half Drawdown" |
|---|---|---|
| **A_T screener** | ✅ Value/quality selection, defensive sleeve | — |
| **NS-7 momentum** | ✅ **Primary**: growth/momentum selection (R1) — the return engine | — |
| **NS-5 frontier** | ✅ Target weights from efficient frontier | ✅ Risk grades (drift, concentration) feed NS-6 signals |
| **NS-5 regime** | ✅ Regime-aware sizing of growth:value allocation | ✅ Regime budget factors in NS-6 multiplier |
| **NS-6 scenario** | ✅ PM's decision tool for adding alpha-generating positions | ✅ Shows drawdown cost of every scenario |
| **NS-6 enforcement** | — | ✅ **Primary**: fast de-risk (R3), graduated exposure reduction, circuit breakers (future) |
| **NS-6 options** | ✅ Covered call income (Phase 3) | ✅ Protective put overlay (Phase 2) |
| **NS-6 drift** | ✅ Regime-gated trim/add recommendations (vs NS-5 frontier — R2b) | — |
| **NS-2 HMM** | ✅ "Does this stock have gas?" + per-name regime gate for NS-7 | — |
| **A_T options data** | — | ✅ Pricing for puts and calls |

### Indirect Contributions

| Service | Contribution |
|---|---|
| **NS-1 CP** | Design precedent: VIX-smile pattern directly informed NS-6's fast de-risk (validated 16yr). No longer the primary drawdown mechanism — NS-6's graduated model is the evolution. |
| **NS-3 rotation** | Sector-level context. Supplementary: if NS-6 drift recommends adding to an underweight and NS-3 ranks that sector #1, confidence increases. Not critical. |
| **A_T macro** | Daily macro context (VIX, yield curve, FRED) feeds NS-6's fast de-risk VIX level + NS-5 regime axis. |
| **A_T sentiment** | Market fear/positioning context (put/call, short interest) for PM awareness. |

## 12. Data Flow (v2)

```
                    A_T SCREENER                  NS-7 MOMENTUM
                    (value/quality)               (growth) ← NEW
                         │                            │
                         ▼                            ▼
                    ┌─────────────────────────────────────┐
                    │         JOINT UNIVERSE               │
                    │    value sleeve ∪ growth sleeve     │
                    │   regime-conditional allocation     │
                    └─────────────────┬───────────────────┘
                                      │
                    ┌─────────────────▼───────────────────┐
                    │           NS-5 FRONTIER              │
                    │  frontier.json (R2b — persisted)    │
                    │  Target weights, cov_matrix,         │
                    │  efficient_frontier_points            │
                    └─────────────────┬───────────────────┘
                                      │
         ┌────────────────────────────┼────────────────────────────┐
         │                            │                            │
         ▼                            ▼                            ▼
   ┌──────────┐              ┌────────────────┐           ┌──────────────┐
   │ NS-2 HMM │              │ NS-5 GRADE     │           │ NS-5 TAX     │
   │ Regime   │              │ Concentration  │           │ Lot data     │
   │ per      │              │ Drift          │           │ TLH avail.   │
   │ ticker   │              │ Regime state   │           │ Tax profile  │
   └────┬─────┘              │ Vol, VaR, corr │           └──────┬───────┘
        │                    └───────┬────────┘                  │
        │                            │                           │
        └────────────────────────────┼───────────────────────────┘
                                     │
                                     ▼
                    ┌─────────────────────────────────────┐
                    │              NS-6                    │
                    │                                     │
                    │  ┌───────────────────────────────┐  │
                    │  │ Scenario Engine               │  │
                    │  │  • A_T screener feed (R2c)    │  │
                    │  │  • Funding paths              │  │
                    │  │  • Tax impact per path        │  │
                    │  │  • Drawdown impact projection │  │
                    │  └───────────────────────────────┘  │
                    │                                     │
                    │  ┌───────────────────────────────┐  │
                    │  │ Drawdown Enforcement          │  │
                    │  │  • Price feed (R2a)           │  │
                    │  │  • Fast de-risk (R3)          │  │
                    │  │  • Put overlay decision       │  │
                    │  │  • Call gate                  │  │
                    │  │  • Circuit breakers (future)  │  │
                    │  └───────────────────────────────┘  │
                    │                                     │
                    │  ┌───────────────────────────────┐  │
                    │  │ Drift Alerts                  │  │
                    │  │  • vs NS-5 frontier (R2b)     │  │
                    │  │  • NS-2 regime context        │  │
                    │  │  • Urgency classification     │  │
                    │  └───────────────────────────────┘  │
                    └─────────────────┬───────────────────┘
                                      │
                                      ▼
                    ┌─────────────────────────────────────┐
                    │         PM DECISION                  │
                    │   Review → Adjust → Sign → Execute   │
                    └─────────────────────────────────────┘
```

**Key data-flow changes from v1:**
1. **Two selectors feed a joint universe** — NS-7 (growth) alongside A_T (value); allocation sized by regime.
2. **NS-5 frontier is persisted** (`frontier.json`) so NS-6 reads it without importing NS-5 (decoupled file-read pattern, matching portfolio-source).
3. **A_T screener feeds NS-6 scenario** via the dashboard (R2c) — not server-to-server, dashboard-to-server.
4. **Price feed (yfinance) → NS-6 enforcement** (R2a) — the missing live data pipe; VIX from the same feed enables fast de-risk (R3).

---

## 13. Recommendations

Ranked, with the baseball mandate and the physics constraint in view.

### R1 — Build a growth/momentum selection service (NS-7) — the return engine

The missing production selector. Two validated pieces already point the way:

- **Pure momentum** (+83pp over value, worst-in-class drawdown) is the natural
  complement to NS-6 — momentum for return, NS-6 for the fat-left-tail. This
  is the highest-leverage single move.
- **Fundamental growth screen** — a growth analogue to the value screener:
  revenue growth, ARR, gross-margin expansion, forward-estimate momentum. The
  value screener's four frameworks exclude these by construction; a growth
  screen selects *for* them.

Promote `selection_construction_experiment.py` from experiment → spec → service.
The walk-forward already proved momentum is the winning selector; the work is
spec + productionization, not research.

### R2 — Wire the live data pipeline (the connective tissue)

The highest-ROI engineering work, and it is all plumbing:

1. **Price ingestion → enforcement.** A daily price feed (yfinance is already
   in the stack) feeding `current_drawdown` and `spy_drawdown` into
   `_enforcement_status()`. Without this, the drawdown engine is decorative.
2. **NS-5 frontier → drift target.** Replace `DEFAULT_WEIGHTS` with a real
   frontier read (NS-5's `portfolio_store`/`frontier.py` output). Decoupled
   file read is fine (matches the portfolio-source pattern).
3. **Screener verdict → scenario.** Populate `screener_scores` from the A_T
   screener (already produces agreement scores) so "should I add X" actually
   shows X's screener verdict.

### R3 — Wire the fast de-risk into the live enforcement loop

Swap the Phase 1 `compute_exposure_multiplier()` call for the validated
`fast_derisk_exposure()` (floored VIX smile, no hard zero). This is a small
code change with a large, measured effect (Sharpe 0.70 → 0.98 on growth).

### R4 — Parameterize the frontier point (the PM decision)

The three switchable profiles are built. The remaining step is to **settle the
target point**: the evidence says the achievable frontier is roughly
"growth basket + sized non-equity sleeve + fast de-risk," yielding Sharpe
1.33 with drawdown somewhere between −25% and −40% depending on sleeve size —
or GMV for a survival mandate. This is a risk-tolerance decision, not code.
NS-6's `theta` (hard_floor, crisis_floor, growth:sleeve split, regime tilt) is
already parameterized to execute whatever point is chosen.

### R5 — Reframe the benchmark (the honest bar)

"Beat cap-weighted SPY with a 12-name drawdown-protected book" is
structurally near-impossible — SPY's return *is* its mega-cap-growth
concentration. The honest bar is: **beat the growth/quality universe you
actually hold, with half the drawdown.** Momentum already achieves +83pp over
value on this bar. Keep SPY as the calibration reference, but stop using
"beat SPY" as the pass/fail gate on a book that deliberately holds fewer
names and a defensive sleeve.

---

## 14. What We Are NOT Building

| Exclusion | Reason |
|---|---|
| Intraday/real-time execution | No day trading. Daily close data is the baseball cadence. |
| Short selling / leverage | Amplifies drawdown; works against the primary objective. |
| Factor timing/rotation as a product | NS-3 exists for sector context; a full factor-timing engine is a multi-year research project. |
| Automated execution | PM in the loop by design. Trade tickets are generated, not auto-submitted. |
| Naked options | Long stock only; cash-secured puts only. |
| LLM in the compute path | Budget/multiplier/funding math stays deterministic. |
| More value-screen refinement | The value screen is validated and *sufficient for value*; the gap is growth, not more value. |

---

## 15. Acceptance Gates (Revised)

v1's gates assumed "beat SPY" and "halve drawdown" were co-achievable. The
physics constraint says they are not. Revised:

| Metric | Threshold | Notes |
|---|---|---|
| Return vs **held universe** (not SPY) | Positive excess in ≥7/10 years | SPY is calibration, not the gate |
| Max drawdown ratio | ≤ 0.5× SPY in ≥8/10 years | Unchanged — this is the mandate |
| Sharpe | > SPY | Achievable: growth basket 1.33, fast de-risk 0.98 |
| Conviction concentration | Effective N ≤ ~25 names | Baseball book — replaces the trade-count gate |
| Trades/quarter | Far under 30 (guideline only) | Not a hard gate; low turnover is the design |
| Worst-year excess | > −5% vs held universe | Don't catastrophically underperform any year |

---

## 16. Key Design Decisions

Updated from v1 Appendix B. v2 changes marked.

| Decision | Rationale | Changed from v1? |
|---|---|---|
| **NS-6 owns rebalancing, not NS-5** | NS-5 is governance (what good looks like). NS-6 is operations (what we can do). Clean separation. | Same |
| **NS-7 is a selector, not a weighting engine** | NS-7 emits ranked signals; NS-5 produces weights from the joint universe. Separation: selection ≠ construction. | **New (v2)** |
| **Regime-conditional two-sleeve allocation, not score-blend** | v1's Option B (re-rank value by momentum) and C (momentum-weight) were proven dead. The correct blend is two sleeves sized by regime. | **New (v2) — direct evidence refutation of v1's B/C paths** |
| **Drift alerts are advisory, not orders** | The PM's instinct is correct — trending winners shouldn't be trimmed. NS-2 regime gates the recommendation. | Same |
| **Scenario engine is PM-initiated, not scheduled** | PMs think about portfolio changes when research surfaces an idea, not on a calendar. | Same |
| **Put overlay vs forced selling is an economic decision** | Both achieve drawdown reduction. One costs premium, the other costs taxes + missed recovery. | Same |
| **Covered calls gated by exposure multiplier** | Writing calls when reducing exposure is contradictory — cap upside on uncertain positions. | Same |
| **Graduated de-risking, not binary** | NS-1's binary VIX model works but is coarse. The VIX-smile floor (never zero) prevents missing V-recoveries. | **Refined (v2): fast de-risk replaces slow quarterly multiplier (R3)** |
| **Hard floors are non-negotiable, soft warnings are advisory** | When budget is at 90% consumed, no alpha signal matters. PM can override soft signals but never hard floors. | Same |
| **Benchmark = held universe, not cap-weighted SPY** | v1's gate ("beat SPY") was the wrong bar for a concentrated, drawdown-protected book. Momentum already +83pp over value on the held-universe bar. SPY stays as calibration reference. | **New (v2) — R5** |
| **Two-league grace period (NS-7)** | A stock that transiently dips below eligibility is not dropped — 90-day minor-league grace pauses assessment but preserves data. Anti-churn for the baseball book. | **New (v2)** |
| **No fundamental growth screen in v1 of NS-7** | Price momentum is proven (+83pp). Fundamental growth (revenue growth/ARR) is an unproven hypothesis — belongs in a v2 research phase, not a v1 service. | **New (v2) — design discipline: don't ship unproven methodology** |
| **Data pipeline is decoupled file-read, not service import** | NS-6 reads NS-5's `frontier.json` + NS-5's `portfolios.json` directly (matching the existing portfolio-source pattern). No cross-service imports, no HTTP fragility. | **New (v2) — R2b pattern** |

---

## Appendix A: Gap-to-Recommendation Mapping

How each recommendation addresses the three critical gaps from §5.

| Gap | Severity | Addressed by | Mechanism |
|---|---|---|---|
| **G1** — No growth selection methodology | 🔴 Most critical | **R1** (NS-7 selector) | New service: momentum selection + quality floor + two-league grace + regime-conditional sleeve allocation |
| **G2** — Live data pipeline unwired | 🔴 Critical | **R2** (data pipeline) | Three plumbing flows: price feed → enforcement (R2a), frontier → drift target (R2b), screener → scenario (R2c) |
| **G3** — Wrong de-risk mechanism | 🟠 Important | **R3** (fast de-risk wiring) | One call swap: `compute_exposure_multiplier()` → `fast_derisk_exposure()` in NS-6's live loop |
| — (cross-cutting) | — | **R4** (frontier point) | PM risk-tolerance decision — the three switchable profiles already execute whatever point is chosen |
| — (cross-cutting) | — | **R5** (benchmark reframe) | Methodology constraint — replace "beat SPY" with "beat the held universe" in acceptance gates |

**Execution order** (within recommendations):

1. **R3** — fastest win; one call swap; the validated function already exists in `enforcement.py`;
   prerequisite: R2a (price feed must provide VIX level for the fast de-risk).
2. **R2a → R2b → R2c** — the data pipeline is plumbing both the value screener and NS-7 will need.
3. **R1** — NS-7 selector; designed (spec + core logic + 18 tests in `NS-7_QA/`); the selection layer is the binding constraint.
4. **R4 + R5** — PM decisions, no code; do anytime.

---

## Appendix B: Service Impact

Which existing services each recommendation touches.

| Rec | NS-6 | NS-5 | A_T | Portal | NS-7 (new) |
|---|---|---|---|---|---|
| **R1** — NS-7 selector | Reads NS-7 output as growth sleeve feed | Frontier sizes joint universe (value ∪ momentum) | — | — | **Built** — selector service, port 9271 |
| **R2a** — Price → enforcement | `qa_server.py` + new `price_feed.py` module | — | — | — | — |
| **R2b** — Frontier → drift target | `_drift()` reads `NS-5_PROD/data/frontier.json` | Persist frontier output as a file (already computed weekly) | — | — | — |
| **R2c** — Screener → scenario | Dashboard `fetch()`es A_T screener before scenario API call | — | Existing screener endpoint serves per-ticker agreement (read-only, no new code) | — | — |
| **R3** — Fast de-risk wiring | `_enforcement_status()` one-call swap; `store.py` +1 row for crisis-hysteresis state | — | — | — | — |
| **R4** — Frontier point | Config theta — profiles already parameterized | — | — | — | — |
| **R5** — Benchmark | `ns6_backtest.py` harness config (one-line change) | — | — | — | — |

**Aggregate:**

| Service | Recommendations | Risk profile |
|---|---|---|
| **NS-6** | R2a, R2b, R2c, R3, R4, R5 — all six non-R1 recommendations | 🟡 Medium — all changes are wiring/swaps, no new algorithmic logic |
| **NS-5** | R2b only | 🟢 Low — persist one JSON file that is already computed |
| **A_T** | R2c only | 🟢 Low — dashboard fetches an existing endpoint; no server-side change |
| **Portal** | None | 🟢 None |
| **NS-7** | R1 only | 🟡 New service (follows NS-6 house pattern) |

The sharp finding: **NS-6 absorbs six of seven recommendations**, but every change is a wiring or swap — no new methodology, no new math. The risk is plumbing mistakes, not design errors, which makes it well-scoped for the junior layer.

---

## Appendix C: Evidence Documents Referenced

All in `Project_Nine_Street/NS-6_QA/`:

- `structural_tension.md` — value vs growth decade; no config beats SPY; the two-gate tension.
- `growth_basket_evidence.md` — MAG7 Sharpe 1.33 but −42% DD; fake diversification; the physics constraint.
- `selection_construction_evidence.md` — pure momentum +83pp over value; selection (not weighting) is decisive.
- `regime_conditional_evidence.md` — 36/38 quarters were growth; QQQ +513%; growth is the return engine.
- `fast_derisk_evidence.md` — VIX-smile + floor (Sharpe 0.98) strictly beats slow multiplier (0.70–0.81).
