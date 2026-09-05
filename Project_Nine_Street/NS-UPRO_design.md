# NS-UPRO — Weekly Covered-Call Overlay on UPRO (Design)

**Status:** Design Proposal — research-validated 2026-09-04, PM review pending
**Type:** Strategy design page (no service built yet; calculator spec in §7)
**Underlying:** UPRO (3× daily S&P 500), weekly ATM-5% calls, Monday open → Friday expiry
**Validation basis:** 2010–2026 daily data + CBOE VIX9D measured IV (2011–2026), BS pricing, gross of costs. Full study: `weekly-covered-call-study` skill; data: `upro_daily.csv`, `tqqq_cc_study.csv`, `upro_real_ivrv.csv`.

---

## 1. Executive Summary

NS-UPRO is a **yield overlay**, not a directional strategy: hold a fractional
allocation (w) of UPRO, write 105%-strike weekly calls against it each Monday at
the open, hold to expiration (assignment accepted), keep unallocated capital in
T-bills. Two rules govern every week:

1. **IV gate** — write only when chain IV / RV20 ≥ 1.15. Otherwise hold shares
   unhedged (still at w) and collect nothing.
2. **Vol-scaled sizing** — w = 0.50 / 0.40 / 0.25 by RV20 tercile, cutoffs from
   **rolling 2-year percentiles** (never full-sample — lookahead).

**Why it exists:** TQQQ/UPRO chains persistently price IV well above SPY vol
(live measurement 2026-09-04: UPRO chain IV 40.42% vs VIX9D 11.68% = 3.46×
markup; IV/RV20 = 1.67). Writing calls captures that premium. The overlay's
validated profile (at scale 3.0, the prudent planning number):

| Config | CAGR | Sharpe | MaxDD | Worst week |
|---|---|---|---|---|
| Fixed w=0.50 | 23.0% | 1.16 | -30.6% | -11.1% |
| **Dynamic w (rolling terciles)** | **19.5%** | **1.29** | **-22.9%** | **~-8%** |
| UPRO buy-and-hold | 34.2% | 0.88 | -61.1% | -23.5% |

DD-first alignment: dynamic-w variant keeps MaxDD inside the fund's ≤75%-of-SPY
budget with only 2 losing years in 17 (2018, 2022).

---

## 2. Research Findings (what the backtests established)

### 2.1 The edge is the IV premium, nothing else
- Constant-IV grids show writing is **negative-expectancy at IV < ~50%** on a 3×
  underlying — premium never covers shorted gamma on a +30–38%/yr asset.
- Measured IV/RV (VIX9D ÷ UPRO RV20): **mean 0.45** (p10 0.29, p90 0.63) at SPY
  scale. The premium compresses in high-vol regimes (0.52 low-vol vs 0.35
  high-vol) — hence the gate, not blind writing.
- Always-write at SPY-scale IV: CAGR 0.7%, Sharpe 0.13, MaxDD -47%. The gate
  restores viability at every markup level tested (1.0–3.46×).

### 2.2 Strikes: 105% is the operating point
- CAGR is nearly flat 103–108%; Sharpe peaks 103–106%. 105% chosen and NOT
  re-optimized (overfitting guard).
- Higher strikes raise CAGR but also raise vol — never a free lunch.

### 2.3 Position sizing, not options, controls drawdown
- **Protective puts are a structural drag** (~5–10 CAGR pts/yr at weekly–monthly
  frequency on a 3× ETF; put@90% halved CAGR and *worsened* MaxDD). Rejected.
- **Collars** are the only put-based structure that works (short call finances
  the put; -25% DD), but dynamic-w sizing dominates them on Sharpe at similar DD.
- w-scaling: DD scales ~linearly with w; Sharpe rises as w falls (cash blend).
- **Dynamic w by RV tercile cuts MaxDD ~½ at 3–6 CAGR pts**, and Sharpe *rises*.
  Crash-window verification: avg w 0.25–0.40 during 2011/2018/2020/2022/2025
  events; COVID-window loss -4.7% vs B&H ~-45%.

### 2.4 Methodology scars (do not repeat)
- **Never validate an IV-trading filter on synthetic IV** — circular. v1's
  "variant H" (Sharpe 1.36, MaxDD -19.5%) was manufactured by invented
  regime multipliers. Scratched. All v2 numbers use CBOE VIX9D (measured).
- **Tercile cutoffs must be rolling** — full-sample quantiles embed lookahead;
  honest rolling cutoffs cost ~5 DD points (-15.1% → -20.6%) at same CAGR.
- BS on a 3× daily-reset product violates lognormality — directionally useful,
  magnitudes soft. All results gross; UPRO weekly spreads shave several points.

---

## 3. Strategy Specification

### 3.1 The weekly cycle (Monday open → Friday expiry)

```
Every Monday at open:
  1. Measure:  S0 (UPRO open), RV20, chain IV (ATM weekly), VIX9D (ref)
  2. Gate:     chain_IV / RV20 >= 1.15 ?  else STAND ASIDE (hold shares, no call)
  3. Size:     w from rolling-2y RV20 terciles -> 0.50 / 0.40 / 0.25
  4. Price:    BS call S0*1.05, T = trading-days-to-Friday/252, r = 13-wk T-bill
  5. Execute:  sell calls on w-slice at >= BS fair value (else skip writing)
  6. Hold:     to Friday close, assignment accepted. No stops, no early close.
Friday close: mark; log IV/RV to the IV Log (§5.3)
```

### 3.2 Parameter table (calculator contract)

**Auto-pulled (no PM input):**

| Parameter | Source | Used for |
|---|---|---|
| UPRO Monday open + 21d closes | yfinance / broker | S₀, RV20 |
| RV20 = STDEV(ln returns)·√252 | computed | gate denominator, w terciles |
| Rolling 2y RV20 p50/p75 | computed | w cutoffs (0.50/0.40/0.25) |
| VIX9D | CBOE CSV / `^VIX9D` | markup sanity check |
| Risk-free rate | `^IRX` or fixed 4.5% | BS discount (minor) |
| Trading days to Friday | calendar | T |
| Gate + w decision, BS price, P(ITM), breakeven, scenario grid | computed | outputs |

**PM-entered (manual inputs):**

| Parameter | Why manual | Example 2026-09-04 |
|---|---|---|
| **Chain IV (ATM weekly)** — the one critical input | no free API for UPRO chains | 40.42% |
| Account size / contracts | position sizing | — |
| Actual option bid/ask | fill-quality check vs BS fair value | — |
| Strike override | default 105%, overridable | 105% |
| w override | PM may force lower w | per DD budget |

### 3.3 Decision rules (exact)

| Rule | Trigger | Action |
|---|---|---|
| GATE | IV/RV20 < 1.15 | no write; shares stay at w |
| W-LOW | RV20 < rolling p50 | w = 0.50 |
| W-MID | p50 ≤ RV20 < p75 | w = 0.40 |
| W-HIGH | RV20 ≥ p75 | w = 0.25 |
| PRICE | broker mid < BS fair value | skip write (not paid enough) |
| DD OVERRIDE | NS-6 floor breach on combined book | PM loop; candidate action w→0.25 floor |

### 3.4 Position sizing within the fund

NS-UPRO is a **sleeve-level overlay**: the w-slice of UPRO shares + short calls.
It composes with the fund like an NS sleeve — capital = w × equity allocated to
UPRO in the target book. Premium collected accrues to the sleeve's return stream
so NS-5/NS-PC grading sees it. No leverage, no naked shorts, covered calls only
— consistent with fund constraints (DD-first, no naked/shorts/leverage).

---

## 4. Risk & DD Framework Mapping

| Fund rule | NS-UPRO behavior |
|---|---|
| Capital preservation overrides all | w de-risks automatically with RV; max DD -21 to -23% (rolling) |
| ≤75% of SPY DD | 2022 cost -20.6% vs SPY ~-25% — compliant |
| Max 30 trades/mo soft | ≤4 writes/mo (1/week) — compliant |
| No naked/shorts/leverage | covered calls only; w ≤ 0.50 caps UPRO exposure |
| PM in loop for adds/removes | gate/w changes are rule-based; structural changes (strike, gate level) require PM sign-off |
| NS-6 floors | overlay emits weekly mark; NS-6 sees it as sleeve return stream |

Worst-week profile: ~-8% (rolling) vs B&H -23.5%. Gap-down risk on the w-slice
is the residual: 100 shares + 105 call loses (gap below K) minus premium —
bounded by w, not eliminated.

---

## 5. Known Limitations

1. **Markup scale is a one-week snapshot.** 3.46× measured 2026-09-04 with VIX9D
   near historic lows. Plan on 3.0×; the IV Log (§3.2/§7) accumulates the true
   distribution. Scale 2.0 floor case still gives CAGR 14.4%, Sharpe 0.94,
   MaxDD -27.6% — viable.
2. **IV/RV asymmetry:** the gate uses chain IV vs UPRO RV20. RV20 is trailing;
   post-crash it stays high for weeks (slow re-risk) — accepted whipsaw cost.
3. **Gross of costs.** UPRO weekly spreads are wide; the PRICE rule (write only
   at ≥ BS fair value) is the operational defense.
4. **Thin chains:** ATM weekly IV readable, but strikes beyond ±5% may be
   stale-quoted. Stick to 105% ATM-adjacent.
5. **Not backtested with real fills.** Paper-trade the PRICE rule ≥ 4 weeks
   before capital commitment.

---

## 6. Interaction with Existing Stack

- **NS-PC:** NS-UPRO target book = UPRO shares at w + short-call premium accrual.
  Needs a small extension: NS-PC today writes only long-share books; the short-call
  premium line (cash in, obligation out) must be representable in
  `paper_portfolio.json` (new `short_calls` position type or premium-as-cash
  approximation for v1).
- **NS-6:** sees the sleeve's weekly returns for floor enforcement.
- **NS-5 grading:** MtM daily like other sleeves; premium is return on Friday
  expiry, not smooth daily — expect lumpy sleeve returns.
- **NS-ETF / NS-7 / NS-8:** no interaction (separate sleeve).

---

## 7. Deliverables (proposed build order)

1. **NS-UPRO calculator** (this was the PM's original ask) — spreadsheet-style
   page/workbook: Inputs (yellow = manual: chain IV, account) → Data (auto) →
   Decisions (gate, w, contracts) → Pricing (BS, scenarios, P(ITM)) → IV Log
   (accumulates date/IV/VIX9D/RV20/ratio weekly — the missing dataset).
2. **NS-UPRO service** (later, only after paper-trading validates fills):
   Monday-morning job computing gate/w/price, writing `ns_upro_signal.json`
   for NS-PC, port QA +1 convention.
3. **NS-PC short-call representation** (blocks #2 going live).

---

## 8. Open Questions for PM

1. Strike fixed at 105%, or PM-settable weekly? (Research says fixed; re-optimizing weekly is overfitting bait.)
2. Gate threshold 1.15 — keep, or tighten to 1.25 in W-HIGH regime (premium compresses there: measured 0.35 vs 0.52)?
3. Sleeve capital: fixed dollar, or % of fund equity?
4. Paper-trade duration before service build (recommend ≥ 4 weeks of IV Log).
5. TQQQ variant wanted? Same structure validated (markup likely similar); one registry row if so.
