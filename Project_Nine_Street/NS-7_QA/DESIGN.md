# NS-7 — Growth/Momentum Selection Service (Design Spec)

**v1.0 · August 2026 · Frontier Model**
**Directory: `Project_Nine_Street/NS-7_QA/`**

---

## 1. Purpose

NS-7 is the **return engine** of the Nine Street stack. Its single output is a
ranked, quality-gated, liquidity-screened list of growth/momentum tickers that
feeds NS-5 (frontier → target weights) and ultimately NS-6 (drawdown protection).

It sits beside the A_T value/quality screener in the **selection layer**. The two
are complementary, not competing: the value screener selects the defensive sleeve;
NS-7 selects the growth sleeve.

> **Separation of concerns (the architectural guardrail):** NS-7 optimizes
> *return*. It does NOT build drawdown protection into selection — that is NS-6's
> job. A selector that refuses to pick high-beta growth names would reinvent the
> value-tilt problem this service exists to fix.

---

## 2. The Evidence That Motivates It

From the v2 full-stack review (all experiments run on the *same* NS-6 engine,
2017-2026 walk-forward):

| Selector | Return | Max DD | Beat-SPY years |
|---|---|---|---|
| Value screener (agreement ≥2) | 143.9% | −24.5% | 1/11 |
| **Pure momentum (126-day, broad universe)** | **227.4%** | −27.5% | 4/11 |
| SPY (benchmark) | 314.1% | −33.7% | — |

Two falsified blends (do NOT repeat):
- **B** (re-rank value by momentum): 141.2% — *worse*. The value universe lacks
  the growth leaders; re-ordering cannot conjure them.
- **C** (momentum-weight within value): weak (+6pp). Weighting is not the lever.

The decisive variable is **selection**, and the only production-grade candidate
is **pure momentum over a broad universe** — which is what NS-7 formalizes.

---

## 3. Universe Definition

The selection universe is deliberately **narrower than "all SP500"** and **wider
than "the value screener's picks."** It is the set of large, liquid names from
which momentum is chosen — a liquidity-and-size floor, then momentum does the rest.

### 3.1 Eligibility criteria (all must hold)

| # | Criterion | Threshold |
|---|---|---|
| U1 | Index membership | SP500 constituent **OR** |
| U2 | Market capitalization | **> $50B** |
| U3 | Liquidity | 20-day average daily dollar volume **> 100K shares** |
| U4 | Quality floor | Positive trailing-12m EPS **AND** positive trailing-12m operating cash flow |

U1/U2 are an **OR**: a stock qualifies if it is in the SP500 *or* has >$50B cap
(the two overlap heavily; the OR keeps large non-SP500 names like a pre-indexing
mega-cap available). U3 and U4 are absolute **AND** gates applied to everything.

> Rationale for $50B + 100K/day: momentum over small/illiquid names is where
> survivorship bias and phantom fills live. The baseball mandate is large-cap
> home runs, not micro-cap lottery tickets. The floor also keeps NS-7's picks
> executable at the book's size without market impact.

### 3.2 Two leagues — Major and Minor

Eligibility is **not binary on any single day**. A stock can transiently dip
below a threshold (a volume lull, a cap wobble) without being a genuine exit.
The league system gives a **90-day grace period** to distinguish "noise" from
"signal" — the mechanism that lets a baseball book *wait* instead of churn.

| League | Meaning | Assessment |
|---|---|---|
| **Major** | Passes all four criteria; eligible for momentum ranking | **Active** |
| **Minor** | Failed a criterion, *or* newly met criteria (fresh) | **Paused** |

**Rules:**

1. **Promotion (Minor → Major):** a Minor stock that satisfies all four
   criteria for **90 consecutive calendar days** is promoted. Assessment resumes.
2. **Demotion (Major → Minor):** a Major stock that fails any criterion is
   immediately demoted. Assessment pauses; the grace clock starts.
3. **Fresh-entry rule:** a stock that *first* enters eligibility (e.g. newly
   $50B+, newly in SP500) starts in **Minor** and must spend 90 days there
   before it can be selected. This prevents buying the top of a fresh
   promotion spike.
4. **Expiry (Minor → removed):** a Minor stock that stays out of compliance for
   90 consecutive days is removed from the tracked universe.

> **Data is never deleted on demotion.** Momentum history, price history, and
> league tenure are preserved. When a stock re-promotes, its full history is
> intact — momentum is computed over the *continuous* series, not a reset clock.
> Pausing assessment ≠ wiping the record.

### 3.3 League state machine

```
            meets all 4 criteria, 90 consecutive days
  ┌───────────┐        (fresh-entry also starts here)          ┌───────────┐
  │   MINOR   │ ─────────────────────────────────────────────► │   MAJOR   │
  │ (paused)  │                                                │  (active) │
  └───────────┘                                                └─────┬─────┘
       ▲                                                             │
       │                       fails any criterion (any day)         │
       └─────────────────────────────────────────────────────────────┘
       │
       │  out of compliance 90 consecutive days
       ▼
   ┌───────────┐
   │  REMOVED  │
   └───────────┘
```

---

## 4. Momentum Selection

Operates **only on the Major league**, at each rebalance date.

### 4.1 Signal

- **Lookback:** 126 trading days (~6 months).
- **Skip:** 21 trading days (~1 month). Momentum = `P[t−21] / P[t−126] − 1`.
  The skip avoids the short-term reversal that contaminates raw momentum.
- **Universe:** all Major-league tickers with a full 126-day price series
  (survivorship-aware, point-in-time — see Guardrails).

### 4.2 Ranking & selection

1. Rank Major tickers by skip-month momentum, descending.
2. Take top-N (`NS7_TOP_N`, default 20).
3. Apply the **quality veto** (§5) to the ranked list.
4. Apply **concentration caps** (§5).

The output is `{ticker: momentum_score, rank}` — a *signal*, not a weight.
Weights are NS-5's frontier job, not NS-7's.

### 4.3 The blend with value/quality

NS-7 does **not** mix value and momentum scores inside one universe (proven
dead). It outputs the growth sleeve; NS-5's frontier then sizes the **joint
universe** (value sleeve ∪ growth sleeve) with regime-conditional allocation:

| Regime (NS-5 GDP×CPI) | Growth:sleeve tilt |
|---|---|
| R1/R2 growth | momentum sleeve dominant |
| R3/R4 defensive | value/defensive sleeve dominant |

Per-ticker NS-2 HMM states gate *individual* momentum names: a momentum pick in
a CRISIS/MEAN_REV state is flagged for the PM (not auto-excluded — NS-2 is
advisory, NS-6 enforces).

---

## 5. Guardrails (non-negotiable, expressed as acceptance gates)

| # | Guardrail | Gate |
|---|---|---|
| G1 | Walk-forward OOS validation | Momentum must pass the house acceptance gate (≥7/10 yrs excess vs held universe) before *any* service work |
| G2 | Point-in-time, survivorship-aware, no lookahead | Use `sp500_history.py` survivorship universe; select only on data available at the rebalance date |
| G3 | Quality floor = veto, not pick | Momentum picks still require U4 (positive EPS + CFO); this removes junk but keeps growth leaders |
| G4 | Concentration + effective-N cap | Max 8% per name; min effective-N 15; sector cap 40% |
| G5 | Anti-churn / baseball | Skip-month lookback + league grace (90d) + turnover band; momentum must stay *well under* 30 trades/quarter |
| G6 | Separation of concerns | NS-7 emits signals only; NS-6 owns drawdown. No drawdown logic in the selector |
| G7 | Benchmark reframe | Validated against the held universe + drawdown ratio, not "beat cap-weighted SPY" |

---

## 6. Service Surface (QA)

Matches the NS-6_QA pattern (stdlib `http.server`, CORS from `end_headers()` only):

| Endpoint | Method | Returns |
|---|---|---|
| `/health` | GET | status/env/port |
| `/api/universe` | GET | league counts + membership |
| `/api/major` | GET | Major-league tickers + momentum scores, ranked |
| `/api/leagues/{ticker}` | GET | a single ticker's league, tenure, grace status |
| `/api/select` | GET | top-N ranked output (the NS-5 feed) |

QA port **9271** (following QA = PROD+1; PROD 9270, reserved). Dashboard
`ns7_dashboard.html` — a simple league/momentum table (parity with NS-6's
single-origin dashboard pattern).

---

## 7. File Layout (mirrors NS-6_QA)

```
NS-7_QA/
├── config.py          # thresholds, league params, momentum params, ports
├── universe.py        # eligibility check + league state machine
├── selector.py        # skip-month momentum + quality veto + caps
├── store.py           # league tenure + grace clock persistence (sqlite)
├── qa_server.py       # stdlib HTTP server + endpoints
├── ns7_dashboard.html # league + momentum table
├── data/              # runtime sqlite (gitignored)
└── tests/             # unit tests (league logic, momentum, caps)
```

---

## 8. Open Design Decisions (for PM sign-off)

1. **Top-N = 20** — tune to the effective-N guardrail (G4) and the book's size.
2. **126/21 momentum** vs a 12-month/1-month variant — the experiment used 126d;
   a 252/21 variant is a v1.1 experiment.
3. **Grace period = 90 days** — set here per the user's spec; tune if churn
   (too short) or staleness (too long) shows up in walk-forward.
4. **Quality floor strictness** — positive EPS+CFO is deliberately loose (it is
   a veto, not the value screen's agreement≥2). Tightening risks the "B" trap.

---

## 9. What NS-7 Is NOT

- Not a drawdown engine (NS-6).
- Not a weighting/optimization engine (NS-5).
- Not a regime detector (NS-2 / NS-5).
- Not the value screener's replacement — the value screener still feeds the
  defensive sleeve.
- Not a *fundamental* growth screen (revenue-growth/ARR) — that is a **v2
  research phase**, not v1. It is unproven; NS-7 v1 is price momentum only.

---

## 10. Implementation Status (v1 — 2026-08-13)

**All v1 components implemented by the frontier model (no junior handoff):
pipeline, server, dashboard, walk-forward harness. 49 unit tests passing.**

| Component | File | Status |
|---|---|---|
| Config (thresholds, data paths, cadence) | `config.py` | ✅ |
| Eligibility + league state machine (+ orchestration) | `universe.py` | ✅ |
| Momentum + veto + caps (+ turnover band) | `selector.py` | ✅ |
| League/volume/selection persistence (sqlite) | `store.py` | ✅ |
| Data pipeline (universe → facts → league → momentum → feed) | `pipeline.py` | ✅ |
| HTTP server, port 9271 (QA) | `qa_server.py` | ✅ |
| Dashboard (league + momentum + ticker detail) | `ns7_dashboard.html` | ✅ |
| Walk-forward harness (G1 gate) | `ns7_walkforward.py` | ✅ |
| Daily refresh runner (launchd) | `run_refresh.sh` | ✅ |
| Tests | `tests/` | ✅ 49 passing |

### G1 acceptance gate — PASS (walk-forward, 2016-01 → 2026-07, quarterly)

| Metric | Result | Gate |
|---|---|---|
| Excess vs held universe | **8/11 years** (2016,17,20,22,23,24,25,26) | ≥ 7/10 ✅ |
| Max drawdown | −13.2% vs SPY −9.2% (ratio 1.44) | G7 note ⚠️ |
| Annual book turns | 3.1 (76.9% per quarterly rebalance) | G5 ✅ baseball |
| G4 concentration (naive top-20 equal weight) | effective N 20, max 5% | ✅ |

⚠️ The 1.44× drawdown ratio is the **bare selector** (equal-weight top-20, no
NS-6). The mandate's ≤0.5× SPY gate applies to the FULL stack (NS-7 + NS-5
frontier + NS-6 fast de-risk). This harness isolates NS-7's selection edge.

### Design decisions made during implementation

1. **Last-known-good metric fill (data-quality layer).** SEC extraction gaps
   leave some 10-K rows with None `operating_cf`/`eps`. Strict
   "None = not proven" demoted MCD/GOOG/JPM/MA the day a partial filing
   landed — churning the book on DATA, not fundamentals. Each metric now
   falls back to the most recent filing ≤ as-of that reports it. A *reported*
   negative EPS/CFO still demotes; only missing values are bridged.
   Point-in-time preserved.
2. **Quarterly rebalance (default).** Matches the full-stack review's
   quarterly selector rhythm. Tested vs monthly: both pass G1 (8/11);
   quarterly halves annual turnover (3.1 vs 5.9 turns) and improves the
   drawdown ratio (1.44 vs 1.56). Config `WF_REBALANCE_MONTHS`.
3. **Turnover band (G5).** A held name ranked up to TOP_N + 10 stays in the
   book (config `TURNOVER_BAND`) — don't trim on a transient rank wobble.
4. **Re-admission semantics.** A REMOVED ticker that meets criteria again is
   re-admitted as a FRESH Minor (new 90-day probation, history preserved) —
   per §3.2 rule 3 ("newly $50B+ / newly in SP500 start in Minor").
5. **U3 in the walk-forward** is approximated as satisfied (no historical
   volume in the store; SP500/$50B+ names are structurally ≫100K shares/day).
   The LIVE pipeline enforces U3 with real yfinance volume stored in NS-7's
   own `volume` table; a systemic volume outage waives U3 for that refresh
   (never mass-demote the book on a data outage).
6. **A_T integration is read-only SQLite** (decoupled file-read pattern —
   same as NS-6 reading NS-5's portfolios.json). NS-7 never imports or
   writes A_T modules/stores. Market cap = price × shares_outstanding from
   the point-in-time snapshot (730-day staleness guard, A_T convention).
7. **`/api/major` returns ALL scored Major names** (not just top-N) — the
   selection doc persists `scores` plus the band-filtered `selections`.

### Data flow (live)

```
A_T fundamentals_hist.db ──read-only──► pipeline.py ──► data/ns7.db
  annual (filed-stamped)      (daily,      league + volume + selection rows
  prices (closes)              17:30 ET)         │
A_T sp500.json ─────────────────────────────┐    ├─► data/selection.json (NS-5 feed)
yfinance volume (U3) ───────────────────────┘    │
qa_server.py :9271 ◄── ns7_dashboard.html ◄──┘   (portal tab ns7)
```

### Remaining
- QA deployment (launchd load of `com.ninestreet.ns7.qa` + `com.ninestreet.ns7.refresh`) — pending PM approval.
- PROD (port 9270) via the house release flow.
- v2 research: fundamental growth screen (revenue growth/ARR) — explicitly out of v1.
