# NS-ETF — Combined ETF Strategy Service (Design Document)

**Status:** SHIPPED v4.3.0 (2026-08-22) · PM sign-off received
**Ports:** QA 9293 / PROD 9292 · **Branch of record:** main (PR #57 + trunk-sync #58, tag `v4.3.0`)
**Spec history:** `../research_nsetf.md`, WF evidence `../research_nsetf_walkforward.md` (both gitignored)
**Consolidates:** NS-1 (rotation factors + VIX overlay), NS-4-PROD (trend/composite ratio scoring), NS-3-pattern (soft HMM regime). Predecessor review findings are in the research docs.

## 1. Role in the stack

NS-ETF is a **selection/signal service** in the Nine Street pattern (NS-7/NS-8
shape): scheduled pipeline → persisted store → thin stdlib server + machine-
readable feed. It owns ETF-level allocation signals and feeds:

| Consumer | Uses |
|---|---|
| NS-5 | sleeve shares + weights for frontier sizing (third allocation axis beside growth/value) |
| NS-6 | crisis_mode / exposure_cap state |
| NS-X | signals + weights |
| NS-PC | signals + weights |

Boundary rules (same discipline as NS-7): NS-ETF optimizes ETF-level returns;
drawdown enforcement stays with NS-6; weighting stays with NS-5.

## 2. Adopted allocation — P2R_def15

Walk-forward 2016–2026, quarterly rebalance, 10bps costs, three-policy gate
(P0 baseline / P1 merged / P2R split). Iteration 3 finding: **daily VIX checks
decoupled from quarterly weight rebalances** is what makes the overlay work
(quarterly-only checks were useless — crises resolve between checkpoints).

| Sleeve | Share | Members |
|---|---|---|
| Sector core | **60%** (top-3 momentum) | XLK XLV XLF XLY XLP XLE XLI XLB XLU XLRE |
| Defensive | **15%** | TLT IEF IEI AGG SHY BIL |
| Real asset | **25%** (remainder) | DBC GLD |

Soft regime tilt: SPY < 200d → shift 10pp sectors→defensive.
Evidence: 8.9% CAGR, −8.5% MaxDD = **0.25× SPY DD** (gate ≤0.75×), turnover
8.6/yr (crisis rotations, not churn). The dial is monotone: def15 > def25 >
def35 on return at flat-to-better DD.

## 3. Signal methodology

Deterministic end-to-end; no randomness anywhere.

- **Momentum composite** (`selector.py`): blended 21/63/126d momentum,
  risk-adjusted momentum, SPY relative strength, RSI (supplementary),
  ADX z-score vs own series. Missing data surfaces as error rows — never
  silent defaults.
- **Wilder ADX** (`indicators.py`): standard sum-seeded smoothing (fixes both
  NS-4 variants); hand-computed fixture tests.
- **VIX overlay** (`overlay.py`): spot ≥28 → crisis rotation into
  CRISIS_SAFE {SHY BIL AGG TLT IEI GLD}; exit <23 (hysteresis); exposure cap
  scales with spot/avg ratio; BIL cash floor. Fail-open: missing VIX = no
  rotation, logged.
- **Regime** (`regime.py`): seeded 1-D Gaussian HMM (hand-rolled, no hmmlearn)
  scales conviction ×[0.75, 1.25] — soft, never gates. Fail-open NEUTRAL.
- **Universe notes**: EFA/EEM scored internally but excluded from feeds
  (no intl equity via sleeve channel); SHV removed (BIL duplicate);
  VNQ deferred to v2; sector ratios are an advisory dashboard panel only —
  never sized, never fed to NS-7 (strategy segregation).

## 4. Feed contract — `data/signals.json`

NS-8-shaped; consumers must ignore `advisory_sector_ratios` for sizing.

```json
{
  "as_of": "...", "version": 1, "service": "ns-etf",
  "sleeves": {
    "sector_core": {"signals": {...}, "weights": {...}, "share": 0.6},
    "defensive":   {"signals": {...}, "weights": {...}, "share": 0.15},
    "real_asset":  {"signals": {...}, "weights": {...}, "share": 0.25}
  },
  "signals": {"XLK": 1, ...},
  "weights": {"XLF": 0.268, ...},
  "sample_portfolio": {"notional": 100000, "rows": [
      {"ticker": "XLF", "shares": 466, "last": 57.48, "value": 26785.68}, ...],
    "invested": 99923.92, "cash": 0.0},
  "composite_scores": [{"ticker", "score", "sleeve", "components"}...],  // no CASH_EQ
  "regime": {...}, "crisis_mode": false,
  "vix": {"spot", "avg", "state", "exposure_cap"},
  "advisory_sector_ratios": [...],   // display only
  "events": []
}
```

Sample sizing: whole shares at last stored close; **BIL sized last absorbs the
rounding residual** (book may sit slightly off $100K — PM-approved); cash
field always 0.

## 5. Runtime & operations

- **Server**: stdlib http.server (`nsetf_server.py`), ROUTES-style dispatch,
  CORS from `end_headers()` only. Routes: `/health`, `/api/signals`,
  `/api/vix`, `/api/advisory`, `/api/meta` (live/stale), `/api/performance`
  (curve + VIX series/SMA + risk metrics), `POST /api/advisory/accept`
  (PM rebalance button).
- **Pipeline**: launchd weekdays 17:45 ET via `run_refresh.sh`
  (`com.ninestreet.nsetf.refresh` QA / `.refresh.prod` PROD), CLT py3.9,
  `env -u PYTHONPATH`. yfinance → sqlite → feed.
- **Live/stale rule**: fresh = ran within 1 business day (weekend-safe).
- **Accept button**: POST runs a full pipeline pass; UI green→blue persists
  until reload.
- **PROD specifics** (`NS-ETF_PROD/`): ports default to PORT_PROD; refresh
  runner cd's to `_PROD`; data dir gitignored per env.

## 6. Verification

- Canonical: `env -u PYTHONPATH <CLT py3.9> python3 -m unittest discover -s tests`
  from `NS-ETF_QA/` — 30 tests: Wilder fixtures (hand-computed), determinism
  (RNG untouched), fail-open contracts (VIX/regime/data gaps), feed invariants
  (EFA/EEM never fed; weights sum; whole shares), hermetic end-to-end
  pipeline, markup invariants (axis sides, clean legend labels, advisory
  badge, error-row filter).
- Walk-forward harness: `walkforward.py` (disk-cached closes; rerun offline).

## 7. Parallel-run & retirement

Runs alongside NS-1/3/4 for one quarter of live-shadow validation.
Endgame (PM-approved): retire NS-1 + NS-4 (unplist `com.ninestreet.ns1.*`,
`ns4.*`; remove portal tabs; archive dirs, no deletion); keep NS-3.

## 8. Lessons encoded here (see also research docs)

1. Naive cross-universe merges dilute (P1 dominated 3× confirmed) — same as
   NS-7's B-trap.
2. Crisis protection requires DAILY checks even with quarterly weights.
3. Chart container must match renderer (Plotly needs a `<div>`, not `<canvas>`).
4. CSS selectors must be checked against the actual DOM ancestry
   (`.card` never existed → white button).
5. yfinance single-column frames arrive as DataFrames now — normalize before
   `.tolist()`.
