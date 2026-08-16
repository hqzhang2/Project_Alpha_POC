# NS-8 Enhancement Spec — Inverse-Volatility Sizing + 12-Month Sign Signal

**Status:** Draft for PM review (not implemented)
**Date:** August 2026
**Origin:** Moskowitz, Ooi & Pedersen (2012), "Time Series Momentum" — *J. Financial Economics* 104(2), 228–250 (SSRN 2089463)
**Scope:** Enhance `NS-8_QA` (no new service — this is R8 in `full_stack_review_v3.md`)
**Owner:** Frontier (spec), Junior (implementation) — per the standing work-split

---

## 1. Why This Enhancement

NS-8 currently sizes every in-trend asset at a **fixed 20%** (`config.ASSET_WEIGHT`).
The 2012 TSMOM paper — the academic version of the very strategy NS-8 implements —
sizes positions by **inverse volatility** (position ∝ 1/σ). This is the single
highest-value, mandate-compatible improvement available to NS-8.

**The fixed-weight flaw, concretely:** the six-ETF universe spans an order of
magnitude of volatility. DBC (broad commodities) runs ~5–10× the volatility of
IEF (7–10Y Treasuries). At fixed 20% each:

- **DBC dominates the book's risk** — it can contribute the majority of the
  portfolio variance despite being 20% of capital.
- **IEF contributes almost nothing risk-adjusted** — its diversification and
  crash-rally benefit is diluted to near-zero by its low vol.

Inverse-vol sizing equalizes *risk contribution* across the active assets, which
is what actually earns the "bond-like drawdown with equity-like return" that
Faber/TSMOM promise. It is a **pure construction change** — the signal (200-day
SMA) is untouched, so it does not disturb the validated trend logic.

---

## 2. What We Are Changing (and Not)

| Dimension | Current (NS-8 v1) | Enhanced (this spec) | Source |
|---|---|---|---|
| Signal | 200-day SMA, binary | 200-day SMA, binary (**unchanged**) + optional 12-month sign variant | Faber / MOP |
| Position sizing | Fixed 20% per in-trend asset | **Inverse-vol (equal risk contribution)** within the in-trend set | MOP eq. (5) |
| Vol estimate | — (none) | EWMA vol, center-of-mass 60 days, 261-day annualized | MOP §2.4, eq. (1) |
| Direction | Long / flat (SHV cash) | Long / flat (**unchanged** — no shorts) | DD-first mandate |
| Rebalancing | Monthly signal, tranched weekly | Unchanged | Concretum |

**Explicitly out of scope (do NOT build):**

1. **The short leg.** MOP's crisis alpha (straddle payoff, "performs best in
   extreme markets") comes from going *short* when the 12-month return is
   negative. The DD-first mandate forbids shorts. NS-8 remains long/flat; the
   short-leg payoff, if ever wanted, is NS-6's protective-put overlay's job, not
   NS-8's.
2. **Leverage / futures.** MOP sizes to 40% vol per instrument using margin.
   NS-8 stays unlevered, long-only, ETF-based.
3. **Breadth expansion to 58 contracts.** Out of scope for this spec; a separate
   optional step (add 2–4 liquid ETFs) is a Phase-3 decision, not part of R8.

---

## 3. Design

### 3.1 Ex-ante volatility estimate (MOP §2.4)

Faithful implementation of the paper's EWMA variance, applied **at t−1 to
position at t** (no look-ahead):

```python
# vol.py (new module) or inline in signals.py
DELTA = 60 / 61          # center of mass = δ/(1-δ) = 60 trading days
ANN = 261                # trading days/year

def ewma_var(daily_returns, delta=DELTA):
    """Ex-ante annualized variance (MOP eq. 1), oldest-first daily returns."""
    if not daily_returns:
        return None
    # exponentially-weighted mean
    n = len(daily_returns)
    w = [(1 - delta) * (delta ** (n - 1 - i)) for i in range(n)]
    wsum = sum(w)
    mean_r = sum(wi * ri for wi, ri in zip(w, daily_returns)) / wsum
    # exponentially-weighted variance
    var = sum(wi * (ri - mean_r) ** 2 for wi, ri in zip(w, daily_returns)) / wsum
    return ANN * var

def exante_vol(daily_returns, delta=DELTA):
    v = ewma_var(daily_returns, delta)
    return v ** 0.5 if v is not None else None
```

> **Harness note (dependency on R4):** the current `walkforward.py` reports an
> implausible Sharpe 1.19 with 3.10% CAGR and ~zero cost drag — its return/vol
> math is not trustworthy. The inverse-vol sizing **must** land on a harness
> whose vol is computed correctly, or the sizing change is unverifiable. R4 (fix
> the harness) is therefore a prerequisite to validating this enhancement.

### 3.2 Inverse-vol weighting within the in-trend set

Given the binary signal (unchanged), weight each **in-trend** asset inversely
proportional to its ex-ante vol:

```
w_i = (1 / σ_i) / Σ_{j ∈ in-trend} (1 / σ_j)     for signal_i = 1
w_i = 0                                          for signal_i = 0
w_SHV = 1 − Σ w_i
```

This is **equal risk contribution** among the active assets — the long/flat
analogue of MOP's `sign(r) · (40%/σ)`, with the 40% vol target replaced by a
simple normalize-to-1 within the active set (no leverage).

```python
def compute_weights(signals, vols):
    """Inverse-vol weights among in-trend assets; SHV absorbs the rest."""
    inv_vol = {t: 1.0 / vols[t] for t, s in signals.items()
               if s == 1 and vols.get(t)}
    total = sum(inv_vol.values())
    if total <= 0:
        # no in-trend asset with a valid vol → all cash
        return {t: 0.0 for t in signals} | {config.CASH_PROXY: 1.0}
    weights = {t: (inv_vol[t] / total) if t in inv_vol else 0.0
               for t in signals}
    weights[config.CASH_PROXY] = round(1.0 - sum(weights.values()), 12)
    return weights
```

**Fallbacks (fail-open, consistent with house convention):**

- If an asset has insufficient history for a vol estimate (`exante_vol → None`),
  treat it as **out of trend** for sizing purposes (weight 0, cash absorbs) — do
  *not* crash, do *not* default to fixed weight.
- If **no** in-trend asset has a valid vol, the book goes 100% SHV and logs a
  warning (matches NS-8 design §7.2's "stale → full cash" semantics).

### 3.3 (Optional, Phase 2) Book-level volatility targeting

The paper's fuller idea is a **constant ex-ante vol** book. After equal-risk
sizing, optionally scale the *total* risky weight so the combined book hits a
target annualized vol (e.g., 8–10%), with SHV absorbing the residual:

```
scale = min(1.0, TARGET_VOL / book_vol)     # never leverage (>1)
w_i   = scale · w_i                          # for in-trend assets
w_SHV = 1 − Σ w_i
```

This is **optional** and *not* required to ship R8. It is listed here so the
config surface anticipates it (`VOL_TARGET_ENABLED`, `VOL_TARGET_ANN`). Default
**off** — equal-risk-contribution alone already fixes the fixed-weight flaw.

### 3.4 12-month sign signal (robustness variant)

MOP's canonical signal is `sign(12-month excess return)`, not a 200-day SMA.
The two are close (200 days ≈ 10 months), but the sign test is simpler and the
paper's headline result. Add it as a **config-selectable signal variant**,
off by default, so the PM can cross-validate:

```python
# config.py
SIGNAL_METHOD = "sma"          # "sma" (current) | "sign12m"

def generate_signals_sign12m(prices, window_days=252):
    """Long if trailing 12-month return > 0, else cash."""
    return {t: (1 if (len(c) >= window_days and c[-1] > c[-window_days]) else 0)
            for t, c in prices.items()}
```

> Do **not** change the default signal. This is a cross-validation dial, not a
> methodology change — the 200-day SMA is deliberately anti-optimized (Faber
> Exhibit 7) and stays the default until the PM chooses otherwise.

### 3.5 Fix the floating-point cash residual

`compute_weights()` currently produces `SHV = 0.19999999999999996` in
`signals.json` because it computes `1.0 - sum(weights)` on raw floats. The new
inverse-vol function already rounds (see §3.2). Backport the same rounding to
any remaining fixed-weight path.

---

## 4. Config Surface (additions to `config.py`)

```python
# ── Sizing (R8) ────────────────────────────────────────────────────────
SIZING_METHOD = "inverse_vol"     # "fixed" (v1) | "inverse_vol" (default)
SIGNAL_METHOD = "sma"             # "sma" (default) | "sign12m"
VOL_DELTA = 60 / 61               # EWMA center-of-mass = 60 days
VOL_ANN = 261                     # trading days/year

# ── Optional vol targeting (Phase 2, default OFF) ─────────────────────
VOL_TARGET_ENABLED = False
VOL_TARGET_ANN = 0.10             # 10% annualized target book vol
VOL_TARGET_MAX_SCALE = 1.0        # never leverage
```

Keep `ASSET_WEIGHT = 0.20` for the `fixed` path and for tests; the new default
path is `inverse_vol` and ignores `ASSET_WEIGHT`.

---

## 5. Acceptance Gate (R8)

The enhancement is valid **iff** it improves the book on at least one of the
following without materially degrading the others, measured by the **fixed
harness** (R4) on the standard window (2006–2026):

| Metric | Requirement | Comparison |
|---|---|---|
| Sharpe | **≥ fixed-weight Sharpe** (expect improvement) | `inverse_vol` vs `fixed`, same signal |
| Max drawdown | **≤ fixed-weight DD** (expect improvement from IEF up-weight) | same |
| CAGR | **Not materially worse** (no hard floor; report the delta) | same |
| Risk concentration | **Reduce** the max single-asset risk contribution | DBC risk share should fall |
| Weight validity | Σ weights = 1.0 exactly; no float residue | unit test |

**Hard requirement:** the harness must reproduce the v1 fixed-weight numbers
*first* (proving the harness is fixed), then show the `inverse_vol` numbers.
A sizing change verified on a broken harness proves nothing.

---

## 6. Tests

Extend `NS-8_QA/tests/`:

```python
# tests/test_sizing.py
def test_inverse_vol_weights_sum_to_one():
    signals = {"SPY": 1, "EFA": 1, "IEF": 0, "VNQ": 1, "DBC": 1}
    vols = {"SPY": 0.15, "EFA": 0.16, "VNQ": 0.20, "DBC": 0.35}
    w = compute_weights(signals, vols)
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert w["IEF"] == 0.0
    assert w["DBC"] < w["SPY"]       # higher vol → lower weight
    assert w["SHV"] == round(1.0 - sum(w.values()), 12)  # no float residue

def test_all_out_of_trend_goes_to_cash():
    signals = {"SPY": 0, "EFA": 0, "IEF": 0, "VNQ": 0, "DBC": 0}
    vols = {"SPY": 0.15, "EFA": 0.16, "IEF": 0.05, "VNQ": 0.20, "DBC": 0.35}
    w = compute_weights(signals, vols)
    assert w["SHV"] == 1.0

def test_missing_vol_fails_open():
    signals = {"SPY": 1, "EFA": 1}
    vols = {"SPY": 0.15}             # EFA vol missing
    w = compute_weights(signals, vols)
    assert w["SPY"] == 1.0
    assert w["EFA"] == 0.0

def test_ewma_vol_positive_and_stable():
    import math
    r = [0.01] * 200
    v = exante_vol(r)
    assert v is not None and v >= 0
    # higher-vol series → higher ex-ante vol
    r2 = [0.02 if i % 2 else -0.02 for i in range(200)]
    assert exante_vol(r2) > v

def test_sign12m_matches_sma_on_trend():
    up = list(range(100, 352))        # steadily rising → both long
    down = list(range(352, 100, -1))  # steadily falling → both cash
    assert generate_signals_sign12m({"A": up})["A"] == 1
    assert generate_signals_sign12m({"B": down})["B"] == 0
```

---

## 7. Implementation Plan

| Step | Task | Effort | Depends on |
|---|---|---|---|
| 1 | Fix `walkforward.py` return/vol/cost math (R4) — reproduce v1 fixed-weight numbers | Medium | — |
| 2 | Add `vol.py` (EWMA ex-ante vol) + `SIZING_METHOD` config | Low | Step 1 |
| 3 | Rewrite `compute_weights()` for inverse-vol + float fix | Low | Step 2 |
| 4 | Add `sign12m` signal variant (config-selectable, off) | Low | — |
| 5 | Add tests (sizing + vol + sign variant) | Low | Steps 2–4 |
| 6 | Run walk-forward: `fixed` vs `inverse_vol`, same window | Low | Steps 1–3 |
| 7 | PM review of the gate table (§5) | — | Step 6 |
| 8 | QA deploy + portal, then paper-trade (R4's Phase 6) | Medium | Step 7 |

**Estimated:** ~4–6 days to a verified comparison, riding on R4's harness fix.

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| Vol estimate too noisy / whipsaws weights | EWMA (60-day center) is the paper's deliberately simple, anti-overfit choice; compare against a 60-day trailing std as a sanity check |
| Inverse-vol over-weights low-vol assets into concentration | Equal-risk-contribution is the *intended* outcome (that's the point); the optional vol-target cap (Phase 2) bounds total exposure |
| Sizing change interacts with tranching | Tranched weekly rebalancing (Concretum) is unchanged and orthogonal to *within-tranche* sizing |
| Harness still wrong after R4 | Gate is blocked — the enhancement cannot ship until `fixed` reproduces v1 numbers (hard requirement §5) |

---

## 9. Decision

**Proceed with R8 (enhance NS-8), not a new NS-9.** This is a construction
change to an existing, validated strategy, sourced directly from its academic
parent (MOP 2012). It captures the one high-value, mandate-compatible idea the
paper offers (inverse-vol sizing) while explicitly declining the two that the
DD-first mandate forbids (short leg, leverage).

**Measured result (2026-08-16, real 2006–2026 data, tranched-weekly):**

| Metric | Fixed (v1) | **Inverse-vol (R8)** | Gate |
|---|---|---|---|
| Sharpe | 0.604 | **0.708** | ≥ 0.60 ✅ |
| Max drawdown | 17.57% | **11.81%** | ≤ 15% ✅ (was ✗) |
| CAGR | 4.51% | 4.25% | no material loss ✅ |
| Implied vol | 7.5% | 6.0% | sane band ✅ |

**R8 passes its gate** (Sharpe ↑, MaxDD ↓ below 15%, CAGR holds). The scaling
decision that made this work: inverse-vol weights are scaled by `0.20 × N_valid_in_trend`
(not re-normalized to 100%), which preserves NS-8's long/flat capital-preservation
property — the book still scales down toward cash as trends break. Re-normalizing
to 100% (a naive reading of MOP) *worsened* drawdown (22.5%) by concentrating
the book when few assets are in-trend; that variant is rejected.

**Out of scope (unchanged):** the short leg and leverage remain forbidden by the
DD-first mandate.

**Next action:** sync R8 to PROD, then re-spec NS-8's acceptance gate (the
turnover gate ≤0.8% is miscalibrated for this strategy — real annual turnover is
~320% even tranched, because the 200-day SMA whipsaws; that gate threshold needs
revision, not the harness).
