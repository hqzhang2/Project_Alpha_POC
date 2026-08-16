# Nine Street — v3 Execution Plan

**Status:** Living roadmap
**Date:** August 2026
**Source:** `full_stack_review_v3.md` (the 8 recommendations R1–R8) + `NS-8_enhancement.md` (R8 spec)
**Owner:** PM (decisions) · Frontier (methodology/signals/stats + specs) · Junior (UI/tests/docs/plumbing)

---

## 1. Purpose

This document is the **single source of truth for execution order** of the eight
recommendations in `full_stack_review_v3.md`. It exists so the sequence and the
dependencies between the recommendations do not live only in chat or in the
review's Appendix A.

**How to use it:** update the status ledger (§8) as work progresses. When a
recommendation ships, move it from "pending" to "done" with the release tag. Do
not reorder the phases without updating the dependency graph (§3) — the order is
not arbitrary; it is forced by the critical path.

---

## 2. The Eight Recommendations at a Glance

| Rec | Title | Status (2026-08-15) | Owner | Spec? |
|---|---|---|---|---|
| **R1** | Combined-fund walk-forward (the headline deliverable) | ⏳ NOT STARTED | Frontier | No |
| **R2** | Wire NS-5 frontier into the blend (replace equal-weight) | ⏳ DEFERRED | Frontier + Junior | No |
| **R3** | Unify the strategy evaluation framework | ⏳ NOT STARTED | Frontier + Junior | No |
| **R4** | Fix the NS-8 walk-forward harness | ✅ DONE | Frontier | No |
| **R5** | Migrate the live paper book to the actual strategies | ⏳ NOT STARTED | Junior + PM | No |
| **R6** | Fix the NS-6 PROD price feed (`TypeError`) | ✅ DONE | Junior + Frontier review | No |
| **R7** | Settle the benchmark | ✅ DONE | PM | No |
| **R8** | Enhance NS-8 with inverse-vol sizing (+ 12-mo sign variant) | ✅ DONE | Frontier + Junior | ✅ `NS-8_enhancement.md` |

**Only R8 has a spec today.** R1, R2, R3, R5, R6, R7 exist only as recommendation
paragraphs in the v3 review. Specs are drafted as each phase begins.

---

## 3. Dependency Graph

```
R6 (fix NS-6 PROD feed)       ── no deps ──► do immediately (urgent)
R4 (fix NS-8 harness)         ── no deps ──► do immediately (parallel w/ R6)
R7 (settle benchmark)         ── PM decision, anytime ──► gates judging R1's output

R8 (NS-8 inv-vol sizing)      ── deps: R4 (needs a harness whose vol is correct)
R3 (unify eval framework)     ── no hard deps, but feeds R1

R1 (combined-fund WF)         ── deps: R3 (clean inputs) + R4 (trustworthy NS-8) + R8 (correctly-sized NS-8)
R2 (wire frontier into blend) ── deps: R3 (clean inputs) + R1 (to validate)
R5 (migrate live book)        ── deps: R1 + R2 (must know what the book should hold first)
```

**Reading it:** R6 and R4 are independent and parallelizable. R7 is a decision,
not code, and can happen any time. The critical path is **R4 → R8 → R3 → R1 → R2 →
R5**. R7 gates the *interpretation* of R1, not its construction.

---

## 4. Phased Execution

### Phase 0 — Stabilize (two parallel fixes + one decision)

| Item | Action | Owner | Acceptance |
|---|---|---|---|
| **R6** | Fix the `TypeError: Cannot convert numpy.ndarray to numpy.ndarray` in NS-6 PROD `price_feed.py`; add a regression test that exercises the **PROD path** (not just QA) | Junior (fix) + Frontier (review — feeds enforcement) | PROD drawdown reads a real, non-zero `current_dd`; regression test catches the exact failure |
| **R4** | Fix `NS-8_QA/walkforward.py` return/vol/cost math; reproduce the v1 fixed-weight numbers as a sanity anchor | Frontier | Harness reports a plausible Sharpe (not 1.19 w/ 3.1% CAGR), non-zero cost drag, and correctly simulates tranched-weekly |
| **R7** | PM decides the benchmark | PM | ✅ Decided (2026-08-16): "outperform cap-weighted SPY, ≤0.75× SPY drawdown; SPY is the benchmark" |

**Why Phase 0 first:** R6 is the only *urgent* item (a blind protection floor is a
live risk, not a future one). R4 is the precondition for every downstream NS-8
number. Both are fixes, not new methodology, and both unblock the rest.

### Phase 1 — NS-8 enhancement (R8)

| Item | Action | Owner | Acceptance |
|---|---|---|---|
| **R8** | Implement `NS-8_enhancement.md`: EWMA ex-ante vol + inverse-vol sizing + float fix + `sign12m` variant (off by default) | Frontier (signals) + Junior (tests) | Gate table in the spec §5: `inverse_vol` ≥ `fixed` on Sharpe and DD, no material CAGR loss, Σ weights = 1.0 |

**Why after R4:** a sizing change verified on a broken harness proves nothing.

### Phase 2 — Unified evaluation framework (R3)

| Item | Action | Owner | Acceptance |
|---|---|---|---|
| **R3** | One common walk-forward harness + one common gate schema across all strategies; emit a consistent per-strategy return/vol/correlation vector | Frontier (methodology) + Junior (tests/docs) | Every strategy reports the same metric shape; "passes its gate" means the same thing per sleeve |

**Why before R1:** the combined walk-forward needs clean, comparable per-strategy
inputs. Running it on bespoke, inconsistent gates would produce a number you
can't trust.

### Phase 3 — The headline deliverable (R1)

| Item | Action | Owner | Acceptance |
|---|---|---|---|
| **R1** | Combined-fund walk-forward: `Σ(strategies) → NS-5 sizing → NS-6 floor`, 2016–2026 (2006–2026 where data allows), emitting one equity curve / max DD / DD ratio / Sharpe / excess-vs-SPY | Frontier | One combined drawdown ratio and one combined excess-vs-held-universe become the two headline metrics gating every future release |

**This is the answer to the mandate.** Depends on R3 (clean inputs), R4
(trustworthy NS-8), R8 (correctly-sized NS-8).

### Phase 4 — Wire the construction layer (R2)

| Item | Action | Owner | Acceptance |
|---|---|---|---|
| **R2** | Wire NS-5's frontier (Ledoit-Wolf + SLSQP) into `sleeve_blend.py`, replacing equal-weight-within-sleeve | Frontier (sizing) + Junior (wiring) | The live blend sizes sleeves by return/vol/correlation, validated against R1's harness |

**Why after R1:** the frontier is the one real *algorithmic* step; it must be
validated against the combined harness before it becomes the live sizing path.

### Phase 5 — Run the fund (R5)

| Item | Action | Owner | Acceptance |
|---|---|---|---|
| **R5** | Migrate `paper_portfolio.json` from the NS-1 ETF book to the fund's actual output (NS-5 frontier/blend, regime-sized, NS-6 enforcing) | Junior (plumbing) + PM sign-off | The live scoreboard scores a book the fund actually built |

**Why last:** migrating the book before R1/R2 is done would be migrating to a
book the fund doesn't yet know how to build.

---

## 5. Critical Path

```
R4 ──► R8 ──► R3 ──► R1 ──► R2 ──► R5
```

- **R6** and **R7** are off the critical path (R6 is parallel-urgent; R7 is a
  parallel decision).
- The fastest defensible route to "does the fund beat SPY with half the drawdown"
  is R4 → R8 → R3 → R1. **Do not shortcut any of these three prerequisites.** A
  combined WF built on a broken harness (R4 skipped), wrong sizing (R8 skipped),
  or inconsistent inputs (R3 skipped) yields a number you cannot trust — worse
  than no number.

---

## 6. Work-Split Mapping

Standing rule: **frontier = methodology/signals/stats + writes specs; junior =
UI/tests/docs/plumbing, never touches signal/backtest functions.**

| Rec | Frontier | Junior |
|---|---|---|
| R1 | Combined WF harness + interpretation | — |
| R2 | Sizing logic (frontier wiring) | Blend plumbing + portal |
| R3 | Harness + gate *schema* (methodology) | Test/doc scaffolding |
| R4 | Harness math (stats) | — |
| R5 | — | Book migration plumbing + PM sign-off |
| R6 | Review (feeds enforcement) | Bug fix + regression test |
| R7 | — | — (PM decision) |
| R8 | Vol model + sizing logic | Tests |

---

## 7. Risks & Blockers

| Blocker | Impact | Notes |
|---|---|---|
| R4 harness fix is harder than it looks | Delays the entire critical path | The "implausible Sharpe 1.19 / 3.1% CAGR / ~zero cost drag" triad suggested the return-series construction was wrong, not just a bad annualization — confirmed and fixed (R4) |
| R7 benchmark undecided when R1 lands | R1's result can be *built* but not *judged* | ✅ Resolved 2026-08-16 — benchmark decided ("outperform cap-weighted SPY, ≤0.75× SPY DD"); R1 can now be judged on arrival |
| R3 is a large cross-cutting change | Could balloon scope | Scope it as *one common harness + one gate schema*, not a rewrite of every strategy's internals |
| Short-leg crisis alpha (MOP 2012) stays off the table | The fund forgoes the paper's biggest alpha | Deliberate — DD-first forbids shorts; NS-6's put overlay is the only sanctioned path |
| Sibling-session working-tree clobber | Uncommitted edits lost | Never `reset --hard` without verifying; re-verify commits land on the *active* branch (may switch mid-session) |

---

## 8. Status Ledger

| Rec | Phase | Status | Release tag | Date |
|---|---|---|---|---|
| R6 | 0 | ✅ **DONE** — env self-check live (QA+PROD); PROD DD now reads −0.0276 | feature/v4.0 (unreleased) | 2026-08-16 |
| R4 | 0 | ✅ **DONE** — real-data harness, tranching, cost/Sharpe fix (QA+PROD) | feature/v4.0 (unreleased) | 2026-08-16 |
| R7 | 0 | ✅ **DONE** — benchmark decided (revised 2026-08-16): "outperform cap-weighted SPY, ≤0.75× SPY DD, SPY is the benchmark" | — (PM decision) | 2026-08-16 |
| R8 | 1 | ✅ **DONE** — inverse-vol sizing + sign12m (QA+PROD); MaxDD 17.6%→11.8%, Sharpe→0.708 | feature/v4.0 (unreleased) | 2026-08-16 |
| R3 | 2 | ✅ **DONE** — sleeve streams unified (both sleeves emit common return rows; R1 combines them via subprocess to avoid the config-name collision) | feature/v4.0 (unreleased) | 2026-08-16 |
| R1 | 3 | ✅ **DONE** — combined-fund walk-forward (first assembly); DD gate PASS (0.72× SPY), Return gate FAIL (3/11 vs SPY) | feature/v4.0 (unreleased) | 2026-08-16 |
| R2 | 4 | ✅ **DONE** (Phase 4) — frontier sizer built (`NS-5_QA/frontier_sizing.py`), validated on real data, NOT wired into live blend (per PM: feeds revamped R1) | feature/v4.0 (unreleased) | 2026-08-16 |
| R5 | 5 | ⏳ **SCOPED TO NS-X** — migrate paper book to `Σ(NS-X strategy weight × strategy book)`, paper-trade only; centralized strategy-data DB deferred to v4 | — | — |

**Phase 0 result (2026-08-16):** R6, R4, R7 all complete. **Phase 1 (R8):**
complete — inverse-vol sizing cuts NS-8 MaxDD below the 15% hard gate (11.8%)
and raises Sharpe to 0.708 on real data; NS-8 gate re-spec'd
(`NS-8_gate_respec.md`) — turnover is now a reported diagnostic (the control is
cost drag at 2bp/side), MaxDD ≤15% is the single hard gate. All committed on
`feature/v4.0`. Next: R3 (unify evaluation) → R1 (combined-fund walk-forward),
now with the benchmark decided.

---

## 9. Next Actions (in order)

1. **R6** — draft spec + fix the PROD price feed (urgent).
2. **R4** — draft spec + fix the NS-8 harness (unblocks the critical path).
3. **R7** — PM decides the benchmark (parallel, no code).
4. **R8** — implement `NS-8_enhancement.md` once R4's harness is trustworthy.
5. Then R3 → R1 → R2 → R5 in sequence.

---

*This plan is a living document. Update the status ledger as each recommendation
moves; keep the dependency graph and phases in sync if anything is reordered.*
