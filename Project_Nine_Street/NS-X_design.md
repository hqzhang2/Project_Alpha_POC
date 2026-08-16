# NS-X — Strategy Allocation Service (Strategy Registry + Rotation)

**Status:** Design Proposal (Frontier Model) — PM-approved direction (2026-08-16)
**Date:** August 2026
**Decision:** Proceed with **NS-X as a NEW, additive service** — compartmentalized,
minimal impact on the existing stack. Rotation signal = **relative momentum
across strategies**.
**Serves:** NS-9/10 (future strategies) + the R1 revamp (which will consume this
allocation layer).

---

## 1. Executive Summary

The Nine Street stack is a multi-strategy, multi-asset fund, but it has no layer
that decides **how much capital goes to each strategy**. Today that split is
hardcoded (R1's regime tilt) or absent. NS-X fills that gap: it is the
**strategy-level allocator** — the multi-strategy analogue of what NS-5 does for
holdings.

NS-X owns:
1. **A Strategy Registry** — a declarative table of every strategy (NS-7, A_T,
   NS-8, NS-1, NS-3, and future NS-9/10), each exposing a target-book producer
   and a return stream.
2. **A Rotation Signal** — **relative momentum across strategies**: overweight
   strategies whose recent return is trending, underweight those that aren't.
   This is the strategy-level equivalent of NS-7's stock momentum.
3. **The Allocation Output** — `{strategy: weight}` that feeds NS-5 (which then
   sizes within each strategy) → the combined fund book.

**Key architectural stance: additive / compartmentalized.** NS-X reads from
existing services and writes one allocation file. It does **not** change NS-7,
A_T, NS-8, NS-5, or NS-6 internals. Adding NS-9/10 later = one registry row, no
stack surgery.

---

## 2. The Problem NS-X Solves

### 2.1 The missing strategy-allocation layer

| Layer | Sizes | Status |
|---|---|---|
| Selection | which *stocks* (NS-7 momentum, A_T value) | ✅ built |
| Tactical | which *asset classes* (NS-8) | ✅ built |
| **Strategy allocation** | **how much to each *strategy*** | ❌ **missing** |
| Construction | holdings weights within a sleeve (NS-5) | ✅ built |
| Protection | drawdown floors (NS-6) | ✅ built |

Currently "how much to NS-7 vs A_T vs NS-8" is a **hardcoded regime tilt** in the
R1 harness. That is not a strategy decision surface; it is a constant. And it has
no way to incorporate a new strategy (NS-9) without editing the harness.

### 2.2 Why relative momentum

The fund's own philosophy (baseball, low-turnover, ride winners) and its
validated machinery (NS-7 momentum, NS-8 trend) both say the same thing: **recent
relative strength is the signal.** NS-X applies that at the *strategy* level:
strategies currently outperforming the pack get more capital; laggards get less.

This is distinct from the NS-8 *asset-class* rotation (SPY/IEF/DBC in/out) and
from NS-5 *within-sleeve* sizing. NS-X is the **outer loop**: decide strategy
weights first, then let NS-5 and each strategy do their inner work.

---

## 3. Architecture

```
┌─ STRATEGY REGISTRY (declarative, extensible) ──────────────────────┐
│  id        name                target-book source        return src│
│  ns7       equity momentum      NS-7 selection.json       NS-7 WF  │
│  at_val    equity value         A_T screener              A_T hist │
│  ns8       tactical multi-AA    NS-8 signals.json         NS-8 WF  │
│  ns1       ETF cap-preservation NS-1                      NS-1 hist│
│  ns3       sector rotation      NS-3                      NS-3 hist│
│  NS-9/10 (future) → one row each, declaratively                     │
└──────────────────────────────────┬─────────────────────────────────┘
                                   │ per-strategy return series
                                   ▼
                  ┌──────────────────────────────┐
                  │  NS-X ROTATION SIGNAL        │
                  │  relative momentum across    │
                  │  strategies (skip-month,     │
                  │  cross-sectional ranking,    │
                  │  quality floor, cap)         │
                  └──────────────┬───────────────┘
                                 │ {strategy: weight}
                                 ▼
                  ┌──────────────────────────────┐
                  │  NS-5 (construction)         │
                  │  sizes WITHIN each strategy  │
                  └──────────────┬───────────────┘
                                 │
                                 ▼
                  ┌──────────────────────────────┐
                  │  FUND BOOK                   │
                  │  Σ (strategy_w × strategy    │
                  │     target_book)             │
                  └──────────────┬───────────────┘
                                 ▼
                  NS-6 enforcement (floors, caps)
```

**Additive contract:** NS-X reads strategy return streams (files/HTTP, decoupled
like the rest of the stack), computes strategy weights, and writes
`data/strategy_alloc.json`. NS-5 consumes it as one more input. **No existing
service is modified.**

---

## 4. Strategy Registry

### 4.1 Registry schema (`registry.py`)

```python
@dataclass
class Strategy:
    id: str                 # "ns7", "at_val", "ns8", "ns1", "ns3", "ns9", ...
    name: str               # human label
    role: str               # "return" | "diversifier" | "defensive" | "supplemental"
    target_book: str        # producer id (resolved by the data backend)
    return_stream: str      # producer id (resolved by the data backend)
    cadence: str            # "daily" | "monthly" | "quarterly"
    enabled: bool

REGISTRY: List[Strategy] = [
    Strategy("ns7", "Equity Momentum", "return", "ns7_selection", "ns7_returns", "daily"),
    Strategy("at_val", "Equity Value", "defensive", "at_screener", "at_returns", "quarterly"),
    Strategy("ns8", "Tactical Multi-AA", "diversifier", "ns8_signals", "ns8_returns", "monthly"),
    Strategy("ns1", "ETF Cap-Preservation", "defensive", "ns1_book", "ns1_returns", "monthly"),
    Strategy("ns3", "Sector Rotation", "supplemental", "ns3_book", "ns3_returns", "quarterly"),
    Strategy("cash", "Cash / Risk-Off (SHV)", "riskoff", "shv_book", "shv_returns", "daily"),
    # NS-9/10: add a row here. No other code change.
]
```

> **`cash` is mandatory**, not optional — it is the residual risk-off sleeve that
> preserves the DD-first mandate when all risky strategies hit the quality floor
> (§5.2). Its `role` is `riskoff` and it always scores 0 momentum (reference point).

### 4.2 Adding NS-9/10 later

One registry row + a target-book producer + a return-stream producer. The rotation
signal and allocation output handle the rest generically. **That is the scalability win**
— a new strategy is data, not surgery.

### 4.3 Data backend — storage abstraction (deferred centralized DB → v4)

**Decision (PM, 2026-08-16):** NS-X reads strategy return streams through a
**storage backend interface** (`store.get_returns(strategy_id)`), not by reaching
directly into each service's files. This keeps NS-X decoupled from *where* a
strategy's data lives.

**Now (v3/v4.0):** the default backend reads the streams that exist today —
NS-7 / NS-8 walk-forward series, A_T prices — from their current sources
(SQLite caches, JSON caches). Any strategy whose stream isn't wired yet is
**fail-open → weight 0** (its share goes to the survivors), and is enabled as its
stream comes online.

**v4 (deferred):** a **centralized strategy-data store** (a single SQLite/DB that
holds every strategy's return stream + target book) replaces the per-service
file reads. This is the scalable end-state — one source of truth, easy to add
strategies, easy to backfill history — but it is an **infrastructure change
(per-service data migration)**, out of scope for NS-X Phase 1. Noted in
`full_stack_review_v3.md` §13 as a v4 item.

The `store.get_returns()` / `store.get_target_book()` interface is **designed
now** so the v4 DB swap is an implementation detail, not a redesign.

---

## 5. Rotation Signal: Relative Momentum Across Strategies (RISK-ADJUSTED)

### 5.0 Design corrections (frontier review, 2026-08-16)

Four correctness issues were identified in the original §5/§8 and are folded in
here. These are NOT refinements — they are required for the allocator to be
correct:

1. **Risk-adjusted momentum, not raw return.** Strategies span an order of
   magnitude of volatility (NS-7 equity ~15–20% vs NS-8 multi-asset ~5–6%).
   Ranking on raw return is a *beta sort*, not momentum: high-vol strategies
   always win up markets and lose down markets purely from vol. The signal MUST
   be **momentum on vol-normalized returns** (return/σ, the MOP 2012 lesson
   already in our stack via NS-8's `vol.py` / R8 inverse-vol).
2. **A cash/risk-off strategy is mandatory.** With no cash sleeve, NS-X cannot
   express "rotate out of everything" when all strategies have negative momentum —
   silently violating DD-first. **Cash (SHV) is added to the registry.**
3. **Walk-forward validation gate.** The allocator must prove, OOS, that rotation
   beats a static/equal-weight strategy split on the combined fund (Sharpe and/or
   DD ratio). Mechanical gates (sum=1, long-only) are necessary but not sufficient.
4. **Cadence reconciliation.** Strategies refresh at different frequencies
   (daily/quarterly/monthly). The momentum score is only well-defined on a shared
   grid. Rule: **align to the slowest enabled strategy's cadence** (last-known
   value per strategy), with the option to override per-strategy.

### 5.1 Signal (risk-adjusted skip-month momentum)

- **Return normalization:** each strategy's return stream is divided by its
  ex-ante volatility (EWMA, 60-day center — same as NS-8 `vol.py`), so scores are
  comparable across strategies of different vol.
- **Lookback:** skip-month momentum over the *vol-normalized* stream —
  `mom_i = (P̂[t−skip] / P̂[t−lookback] − 1)` where `P̂` is the vol-normalized
  equity index (default lookback 126 days, skip 21 — the same validated params as
  NS-7's stock momentum).
- **Universe:** all enabled registry strategies with a full vol-normalized return
  series, **plus the cash strategy (SHV)** which always scores 0 (reference point).
- **Ranking:** rank strategies by risk-adjusted momentum, descending.

### 5.2 Weighting

| Method | Rule |
|---|---|
| **Relative-momentum tilt** | `w_i ∝ max(mom_i − mom_median, 0)` — overweight only strategies above the cross-sectional median |
| **Quality floor** | a strategy with negative *risk-adjusted* momentum, or an unavailable stream → weight 0 |
| **Concentration cap** | max single-strategy weight ≤ `NSX_MAX_STRATEGY_W` (default 0.40) |
| **Min sleeve** | default 0.03 floor on any enabled strategy with a valid positive-momentum score (optional) |
| **Cash / risk-off** | `w_cash = 1 − Σ_i w_i` — SHV absorbs the residual; **when all risky strategies hit the floor, w_cash = 1.0** (full risk-off, DD-first preserved) |
| **Regime override** | (advisory) the NS-5 regime axis scales the whole risky bucket (risk-on vs risk-off), but the *relative* ranking is momentum-driven |

### 5.3 Output

```json
{
  "as_of": "2026-08-16",
  "rotation": "relative_momentum_risk_adjusted",
  "strategies": {
    "ns7":  0.35,
    "ns8":  0.28,
    "at_val": 0.17,
    "ns1":  0.00,
    "ns3":  0.20,
    "cash": 0.00
  },
  "momentum_scores": {"ns7": 0.82, "ns8": 0.61, "at_val": 0.34, "cash": 0.0, ...},
  "weights_sum": 1.0
}
```

`momentum_scores` are **risk-adjusted** (vol-normalized), so the values are
comparable across strategies. Weights sum to 1.0 (long-only, no leverage), with
`cash` as the residual risk-off sleeve.

---

## 6. Integration with NS-5 and the Fund Book

### 6.1 Interface contract (decoupled file read — house pattern)

- **NS-X writes** `data/strategy_alloc.json` (schema §5.3), versioned (`version`,
  `generated_at`).
- **NS-5 reads** it at each construction step as a *strategy-level bound*: NS-5
  sizes holdings within each strategy, weighted by NS-X's `strategies` dict.
- **Stale/missing handling:** if `strategy_alloc.json` is > 5 days old or missing,
  NS-5 falls back to equal-weight across enabled strategies and logs a warning
  (fail-open, matching NS-8 §7.2's stale semantics).

### 6.2 Fund book composition

```
fund_book = Σ_i (w_i × strategy_i.target_book)
```

where `w_i` is NS-X's strategy weight and `target_book` is each strategy's own
output (NS-7 selection, A_T screener, NS-8 signals, etc.). Overlap sums; weights
renormalized to 1.0.

### 6.3 Precedence (unchanged, extends NS-8 §7.1)

**NS-6 > NS-X > NS-5 > individual strategies.** NS-6 drawdown enforcement
overrides NS-X's allocation; NS-X overrides within-strategy sizing; a single
strategy's signal never overrides the fund layer.

---

## 7. Service Specification (NS-X_QA)

### 7.1 Directory (mirrors NS-6/7/8 house pattern)

```
NS-X_QA/
├── registry.py        # Strategy registry + return-stream loaders
├── rotation.py        # relative-momentum signal + strategy weighting
├── allocator.py       # run_once: fetch returns → momentum → weights → alloc.json
├── store.py           # SQLite: strategy_alloc history (audit)
├── qa_server.py       # FastAPI :9291 (QA) / :9290 (PROD) + dashboard
├── config.py          # thresholds (lookback/skip/cap/floor)
└── tests/
```

### 7.2 Config

```python
MOM_LOOKBACK_DAYS = 126
MOM_SKIP_DAYS = 21
NSX_MAX_STRATEGY_W = 0.40
NSX_MIN_SLEEVE = 0.03
NSX_STALE_DAYS = 5
ROTATION = "relative_momentum_risk_adjusted"
RISK_ADJUST = True              # vol-normalize returns before momentum (mandatory)
VOL_DELTA = 60 / 61             # EWMA center-of-mass (reuse NS-8 vol.py convention)
CASH_STRATEGY_ID = "cash"       # residual risk-off sleeve
MAX_BOOK_TURNS_PER_YEAR = 2.0   # strategy-level rotation turnover cap
```

### 7.3 Outputs / Endpoints

| File / Endpoint | Contents |
|---|---|
| `data/strategy_alloc.json` | `{as_of, rotation, strategies, momentum_scores, weights_sum}` |
| `GET /api/alloc` | Current strategy weights |
| `GET /api/registry` | Registry + per-strategy momentum score + source |
| `GET /api/rotation` | Signal detail (scores, ranking, caps) |
| `POST /api/rebalance` | Manual trigger (guarded) |

### 7.4 Ports

QA **9291** · PROD **9290** (next free pair after NS-8's 9281/9280).

---

## 8. Acceptance Gate

| Metric | Threshold | Rationale |
|---|---|---|
| Weights sum | = 1.0 (±1e-6) | Fully invested (incl. cash), long-only |
| Long-only | all w ≥ 0 | No shorts/leverage (mandate) |
| Concentration | max risky-strategy w ≤ 0.40 | No single-strategy domination |
| Fail-open | missing return stream → weight 0, no crash | House rule |
| Deterministic | same inputs → same weights | Reproducible rotation |
| Risk-adjusted | momentum scores are vol-normalized (comparable across strategies) | Correctness (§5.0 #1) |
| **Walk-forward evidence (HARD GATE)** | rotation beats static/equal-weight split on the combined fund OOS (Sharpe and/or DD ratio ≥ static) | The signal must *prove* it adds value before moving capital (§5.0 #3) |
| **Turnover gate** | strategy-level rotation turnover ≤ threshold (e.g. ≤ 2 full book-turns/yr) at a stated cost model | Rotation costs money; measure and gate it |
| Cadence | momentum computed on a shared grid (slowest-enabled-strategy cadence) | Scores must be contemporaneous (§5.0 #4) |

The **walk-forward evidence gate** is mandatory and non-negotiable: a rotation
signal that cannot be shown, OOS, to improve the combined fund over a static
strategy split is not a signal — it is churn. This is the same discipline NS-7
(G1) and NS-8 (§6) already follow.

---

## 9. Implementation Priority

| Phase | Task | Effort | Dependencies |
|---|---|---|---|
| 1 | Registry + return-stream loaders (incl. cash) | Low | — |
| 2 | Relative-momentum rotation + risk-adjusted weighting | Medium | Phase 1 |
| 3 | allocator run_once + alloc.json | Low | Phase 2 |
| 4 | **Walk-forward validation** (rotation vs static split, OOS) | Medium | Phase 2 — **HARD GATE before any capital moves** |
| 5 | QA server + dashboard (9291) | Low | Phase 3 |
| 6 | Wire into NS-5 construction (strategy-level bounds) | Medium | Phase 4 + NS-5 read |
| 7 | Paper-trade migration (R5: blend + NS-8 book under NS-X weights) | Medium | Phase 6 |
| 8 | PROD deploy (9290) + launchd | Low | QA sign-off |

**Estimated:** 2–3 weeks to a paper-tradable NS-X allocation, **gated on the
walk-forward evidence (Phase 4)**. If rotation does not beat static allocation
OOS, the design is revised before any wiring or paper migration.

---

## 10. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Strategy return streams unavailable/inconsistent | Medium | High | Fail-open; decoupled file/HTTP reads; stale → equal-weight fallback |
| Relative momentum over-rotates (churn) | Medium | Medium | Skip-month lookback + cross-sectional median filter + concentration cap = low-turnover baseball shape |
| Strategy allocation collides with NS-8 asset-class rotation | Low | Medium | Clear precedence (NS-6 > NS-X > NS-5 > strategy); NS-8 is a *strategy* in the registry, not the allocator |
| New strategy adds unexpected behavior | Medium | Low | Registry is declarative; new strategy = data row + its own target-book/return producers |
| R1 revamp reuses this layer | — | Positive | NS-X *is* the allocation layer the revamped R1 consumes — built once, used by both |

---

## 11. What NS-X Does NOT Do

| Exclusion | Reason |
|---|---|
| Not a holdings optimizer | NS-5 owns within-strategy sizing; NS-X sizes *across* strategies only |
| Not a drawdown engine | NS-6 owns floors; NS-X is a strategic allocator |
| Not an asset-class rotor | NS-8 owns that; NS-8 is a *strategy* in NS-X's registry |
| No shorts/leverage | Long-only, weights sum to 1.0 (mandate) |
| No LLM in the compute path | Momentum + ranking + weighting is deterministic math |

---

## 12. Decision

**Proceed with NS-X as a new, additive service** (PM, 2026-08-16):
- New service (not an extension of NS-5) for **scalability + compartmentation** —
  minimal impact on the current workflow/stack.
- **Relative momentum across strategies** is the rotation signal.
- NS-X feeds NS-5; NS-6 enforces; precedence **NS-6 > NS-X > NS-5**.
- Serves NS-9/10 (future) and the R1 revamp (which consumes this layer).

**Next action:** implement Phase 1 (registry) + Phase 2 (rotation) scaffold in
`Project_Nine_Street/NS-X_QA/`.
