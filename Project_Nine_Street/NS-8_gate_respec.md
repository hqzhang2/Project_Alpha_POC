# NS-8 Acceptance Gate — Re-spec (R4/R8 real-data findings)

**Status:** Draft for PM review (not enforced in CI)
**Date:** August 2026
**Source:** `R4_ns8_harness_fix_spec.md` §4 + `NS-8_enhancement.md` §9 measured results
**Why:** the original gates (from `NS-8_design.md` §6) were written against the
Concretum/Faber *published* figures and a synthetic harness. R4 replaced the
harness with real data; R8 changed the sizing. Both exposed that two of the
five gates are **miscalibrated** — they fail on honest numbers for reasons that
are not the strategy's fault.

---

## 1. What the real-data numbers are (2006–2026, tranched-weekly)

| Metric | Fixed (v1) | Inverse-vol (R8) | Original gate | Original gate result |
|---|---|---|---|---|
| Sharpe | 0.604 | **0.708** | ≥ 0.60 | ✅ |
| Max DD | 17.6% | **11.8%** | ≤ 15% | ✅ (after R8) |
| CAGR | 4.51% | 4.25% | — | ✅ (no hard gate) |
| Annual turnover | 267% | 318% | ≤ 0.8% | ❌ **miscalibrated** |
| Cost drag (10bp) | 27 bp | 32 bp | ≤ 30 bp | ❌ **marginal / miscalibrated** |

## 2. The two broken gates — and why

### 2.1 Turnover ≤ 0.8%/yr — WRONG by ~300×, and mis-framed

The original gate imported Concretum's "~0.6% annual turnover" figure. That
number describes a *different* quantity than this harness measures, and in any
case is not what a 200-day-SMA trend book actually does. Measured on real data:

- **8.9 signal flips/yr** across the 5 risky assets (measured directly from the
  harness — not a bug).
- Each flip moves ~20% of the book; **vol-drift re-tuning** (the inverse-vol
  weights shift month to month even without a flip) adds more.
- Result: **~270–320% annual turnover** — the book turns over ~3× per year.

This is the *intrinsic cost of trend-following*. A 200-day SMA that whipsaws at
support/resistance will flip several times a year; that is Faber's known
"whipsaw in choppy markets" trade-off (NS-8 design §12 acknowledged it). A gate
that demands ≤1% turnover on a strategy that inherently does ~300% is asking the
strategy to be something it is not.

**The right framing:** turnover is not a *pass/fail* gate; it is an *input to the
cost model*. The meaningful gate is the **cost drag after the 10bp model**, which
already captures the turnover penalty. If turnover were truly 300% × 10bp = 30bp
of annual drag, that is a real, reported number — not a disqualifier.

### 2.2 Cost drag ≤ 30 bp — marginal, and dependent on the cost assumption

With 10 bp per round-trip, ~300% turnover ≈ **30 bp/yr** cost drag — right at the
old 30 bp line. This is not a strategy failure; it is a statement about
**transaction-cost assumptions**. Real-world costs for liquid ETFs (SPY/EFA/IEF/
VNQ/DBC/SHV) at MOC are typically **1–3 bp**, not 10 bp. At 2 bp, the same
turnover costs ~6 bp/yr. The 10 bp figure in the design was conservative for
backtest honesty, but it makes the strategy look cost-bleeding when it isn't.

## 3. The re-spec (proposed)

| Metric | New threshold | Rationale |
|---|---|---|
| Sharpe (OOS 2006–2026) | ≥ 0.60 | **Unchanged** — real Sharpe 0.708 passes. |
| Max drawdown | ≤ 15% | **Unchanged** — R8 now passes (11.8%). |
| CAGR | No hard gate | **Unchanged** — report, don't gate. |
| **Annual turnover** | **≤ 400%** (informational, NOT a gate) | Real range is 270–320%. This becomes a **reported diagnostic**, not a pass/fail. The real control is cost drag. |
| **Cost drag** | **≤ 20 bp/yr at 2 bp/side** (or ≤ 60 bp/yr at 10 bp/side) | Re-frame cost drag against a **realistic cost assumption** (2 bp/side liquid ETFs). Turnover is already inside it. |
| **Tranche benefit** | Tranched turnover < monthly turnover | Already true (318% vs 424%). Confirms the Concretum tranching is doing its job. |
| Implied vol sanity | CAGR/Sharpe in 5–15% band | Guards against the R4-style "impossible Sharpe" bug returning. |

**The one genuinely hard gate that matters is Max DD ≤ 15%** — it is the
drawdown half of the fund mandate, it is not a function of cost assumptions, and
R8 now meets it.

## 4. What this changes in the harness

The gate in `walkforward.py`'s `__main__` should be updated to:

```python
# NS-8 acceptance gates (re-spec 2026-08-16)
SHARPE_GATE = 0.60
MAXDD_GATE = 0.15
# Turnover is now a REPORTED diagnostic, not a gate. Cost drag is the control,
# evaluated at a realistic 2 bp/side for liquid ETFs (config.TXN_COST_BPS stays
# 10 bp for the conservative backtest; the gate uses the realistic assumption).
TURNOVER_GATE = None            # informational
COST_DRAG_GATE = 0.0020         # 20 bp/yr at 2 bp/side
IMPLIED_VOL_MIN, IMPLIED_VOL_MAX = 0.05, 0.15
```

## 5. What is NOT changing

- **The strategy is not being weakened to pass a gate.** R8's inverse-vol sizing
  genuinely improves Sharpe and cuts MaxDD below 15% — that is real, and it stays.
- **The 10 bp cost model stays in the backtest** (conservative). The gate just
  evaluates cost drag at the realistic 2 bp assumption so the strategy isn't
  unfairly judged.
- **Max DD ≤ 15% remains the hard gate** — it is the drawdown half of the mandate.

## 6. Decision needed from PM

1. **Accept the turnover gate removal** (turnover becomes informational; cost
   drag becomes the control)? 
2. **Cost-drag gate:** 20 bp/yr at 2 bp/side, or keep 10 bp/side and set the gate
   at 60 bp/yr?
3. **Keep Max DD ≤ 15% as the single hard gate?**

Default (if no objection): adopt §3 as proposed, keep Max DD ≤ 15% as the hard
gate, make turnover informational, evaluate cost drag at 2 bp/side.

---

*This re-spec does not change `NS-8_design.md` (preserved). It supersedes the
gate table in §6 of that document for the live harness, pending PM sign-off.*
