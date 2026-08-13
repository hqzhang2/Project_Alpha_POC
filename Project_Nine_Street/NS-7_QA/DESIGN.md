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
