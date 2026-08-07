# NS-5 — Portfolio Evaluation & Concentration Grading Engine

**Status:** v1 (QA) — concentration axis complete
**Service dir:** `Project_Nine_Street/NS-5_QA/`
**QA port:** 9251 · **PROD port:** 9250 (reserved, not deployed)
**Branch:** `feature/v2.6`

---

## What NS-5 does

A standalone portfolio-governance engine. Given a portfolio (holdings + weights) and an
owner parameter vector Θ (policy portfolio, concentration caps, risk profile), it grades
the portfolio on four concentration axes and produces a ranked tweak list.

It is **not** a return-seeking strategy and does **not** depend on NS-1..4. It answers:
*"Is this portfolio still the portfolio you signed up for? If not, what do we change?"*

## Architecture / data flow

```
Yahoo daily closes (SPY IWM VTV VUG MTUM TLT ^IRX) ──► data_fetcher.py ──► data/cache/
        │  (2y window, log returns, CSV cache, weekly refresh cron)
        ▼
factor_returns (MKT SMB HML MOM DUR)  ──►  regression.py  (OLS, 250d window)
        │                                      │
        │                    ┌─────────────────┘
        ▼                    ▼
portfolio.py  ──► run_concentration_grade()  ──►  concentration.py
(parse holdings)        │  ├─ grade_factor_loading()   (β vs policy β*, σ-graded)
                        │  ├─ checks.grade_sector_weights()    (worst-of rule)
                        │  ├─ checks.grade_effective_n()       (N_eff vs floor)
                        │  ├─ checks.grade_tail_correlation()  (worst 5% days)
                        │  ├─ merge_concentration_grade()      (weighted composite)
                        │  └─ generate_tweaks()                (ranked actions)
                        ▼
               scorecard JSON (via qa_server.py /api/grade)
```

## The five factors (v1, frontier-approved)

| Factor | Construction | Proxy |
|---|---|---|
| MKT | SPY return − risk-free (^IRX/252) | SPY |
| SMB | IWM − SPY | IWM, SPY |
| HML | VTV − VUG | VTV, VUG |
| MOM | MTUM − SPY | MTUM |
| DUR | TLT return | TLT |

## Θ parameter vector (`theta.py`)

| Group | Parameters | Default |
|---|---|---|
| Risk | `risk_tolerance`, `target_vol` | moderate, 0.10 |
| Policy | `policy_weights`, `policy_name` | {} |
| Caps | `max_single_name_pct`, `max_sector_pct`, `effective_n_floor` | 0.10, 0.30, 12 |
| Factor grading | `factor_tolerance_sigma` (±2σ flag) | 2.0 |
| Composite weights | `concentration_axis_weights` | factor 40 / sector 25 / eff-N 20 / tail 15 |
| Grade scales | `sigma_grade_bounds`, `letter_score_bounds`, `sector_ratio_bounds` | — |
| Tail corr | `tail_pctile`, `tail_corr_threshold`, `top_n_for_tail` | 5%, 0.7, 5 |
| Sector map | `sector_map` (static GICS for v1) | ~60 large-caps + ETFs |

Override via `theta_mod.load_theta(path="config.json", **overrides)` or per-request in
the API body.

## How to grade a portfolio

### Via API (QA server running)

```bash
curl -X POST http://localhost:9251/api/grade -H 'Content-Type: application/json' -d '{
  "holdings": {"AAPL": 0.14, "MSFT": 0.12, "NVDA": 0.08, "TLT": 0.30, "JPM": 0.05},
  "policy_weights": {"SPY": 0.60, "TLT": 0.40}
}'
```

### Via Python

```python
import sys; sys.path.insert(0, "Project_Nine_Street/NS-5_QA")
import data_fetcher, concentration, theta as theta_mod

factors, closes, _ = data_fetcher.build_factor_returns()
theta = theta_mod.load_theta(policy_weights={"SPY": 0.60, "TLT": 0.40})
result = concentration.run_concentration_grade(holdings, theta,
                                               factor_returns=factors, closes=closes)
print(result["concentration"])   # composite + sub-grades
print(result["tweaks"])          # ranked recommendation list
```

### Portfolio input formats (`portfolio.parse_portfolio`)

- **dict:** `{"AAPL": 0.14, ...}`
- **CSV:** columns `ticker, weight` (also accepts `symbol`/`allocation` names)
- **JSON:** `{"holdings": {...}}` or list of `{"ticker": ..., "weight": ...}`

## How to read the scorecard

```json
{
  "concentration": {
    "composite_concentration_grade": "D",
    "composite_concentration_score": 2.21,
    "sub_grades": {
      "factor_loading":  {"grade": "F", "score": 1.0,  "weight": 0.40},
      "sector":          {"grade": "D", "score": 2.0,  "weight": 0.25},
      "effective_n":     {"grade": "C", "score": 2.79, "weight": 0.20},
      "tail_correlation":{"grade": "A", "score": 5.0,  "weight": 0.15}
    }
  },
  "factor_loading": { "...": "per-factor β, policy β, σ, grade, flagged" },
  "sector":         { "...": "per-sector weight, ratio to cap, grade" },
  "effective_n":    { "effective_n": 6.68, "floor": 12, ... },
  "tail_correlation":{ "...": "flagged pairs on worst 5% of days" },
  "tweaks": [ {"axis": "...", "severity": "critical|high|medium",
               "recommended_action": "...", "rationale": "..."}, ... ]
}
```

**Grade scale:** A = near policy (≤0.5σ) · B = within tolerance (≤1.5σ) ·
C = material deviation (≤2.5σ) · D = significant (≤3.5σ) · F = severe (>3.5σ).
Sector grades use ratio-to-cap (A ≤0.5× cap, B ≤1.0×, C ≤1.25×, D ≤1.5×, F above).
**Worst-of rule:** the composite sector grade is the *worst* sector, not the average.
**Tweaks** are ordered critical → high → medium.

## Refreshing factor data

```bash
env -i HOME=$HOME /usr/bin/python3 run_weekly_refresh.py   # manual refresh
# launchd: com.ninestreet.ns5.refresh (Saturday 08:00) — see deploy/
```

## Drift axis (v2)

The drift axis monitors how the portfolio has moved away from its policy
target over time — a time-series consumer of the same five-factor model.

| Level | Monitors | Flag when |
|---|---|---|
| **Weight drift** | Position weight vs policy band | \|w − target\| / target > 20% (relative) |
| **Risk drift** | Trailing 60d/120d/250d vol, VaR/CVaR(95%) | Trailing vol > long-run × 1.5σ, or VaR/CVaR breach |
| **Style/factor drift** | Factor β on trailing 2yr vs policy β* | \|β − β*\| / se > 1.5, or QQQ corr > 0.90 |
| **Frontier drift** | Long-run vs trailing frontier | Sharpe degradation > 0.15, tangency shift > 15pp, stock-bond corr sign flip |

Composite drift grade = weighted average (15/25/30/30) with severity
green → yellow → orange → red. Drift tweaks are appended to the shared
tweak list with `axis: "drift"`.

Request both axes: `POST /api/grade` with `{"axes": ["concentration", "drift"]}`
(default when omitted). Drift-specific Θ keys — see `theta.py`:

| Key | Default | What |
|---|---|---|
| `drift_band` | 0.20 | ±20% relative weight tolerance per asset class |
| `risk_budget.target_vol` | 0.14 | Annualized σ* (policy risk budget) |
| `risk_budget.var_95_limit` | −0.15 | Daily VaR(95%) limit |
| `risk_budget.cvar_95_limit` | −0.22 | Daily CVaR(95%) limit |
| `risk_budget.vol_spike_sigma` | 1.5 | Trailing vol > long-run × N → flag |
| `style_tolerance.factor_sigma` | 1.5 | \|β − β*\| / se above this → flagged factor |
| `style_tolerance.qqq_corr_threshold` | 0.90 | Correlation to QQQ above this → "this IS QQQ" |
| `frontier_thresholds.sharpe_degradation` | 0.15 | Long-run Sharpe − trailing Sharpe above this → flag |
| `frontier_thresholds.tangency_shift` | 0.15 | Max tangency weight diff → flag |
| `frontier_thresholds.bond_corr_sign_flip` | true | Stock-bond corr sign flip → independent flag |
| `drift_axis_weights` | 15/25/30/30 | weight/risk/style/frontier composite weights |
| `drift_severity_bounds` | green→red | Score → severity mapping (descending) |

## Tests

```bash
cd Project_Nine_Street/NS-5_QA
env -i HOME=$HOME /usr/bin/python3 -m pytest tests/ -q    # 102 tests, all synthetic
```

| File | Coverage |
|---|---|
| `test_phase1.py` | Factor pipeline, log-return NaN contract, OLS recovery, environment monitors |
| `test_phase2.py` | Policy β, factor-loading grading boundaries, missing-SE fallback |
| `test_phase3.py` | Sector worst-of, effective-N linear scale, tail-correlation |
| `test_phase4.py` | **End-to-end pipeline**, edge cases (single/zero/all-same/NaN), **acceptance gate** (determinism, no-NaN JSON, fail-open) |
| `test_frontier.py` | Efficient frontier math, GMV, diversification effect, benchmark anchors |
| `test_store.py` | Portfolio/policy store CRUD, shares→weights conversion |
| `test_drift.py` | Drift checkers (weight/risk/style/frontier), grade/merge, tweak structure |

## Deployment

- **QA:** `deploy/com.ninestreet.ns5.qa.plist` → port 9251 (stdlib http.server)
- **Refresh cron:** `deploy/com.ninestreet.ns5.refresh.plist` → Saturday 08:00
- **PROD:** deferred until v1 is stable; then port 9250 per house convention
- Restart: `launchctl kickstart -k gui/$(id -u)/com.ninestreet.ns5.qa`

## House rules honored

- Deterministic compute — no LLM in the pipeline; results JSON in service dir
- Fail-open: missing factor data → `INSUFFICIENT_DATA`/`N/A`, never a crash
- No-NaN output: scorecards serialize to valid JSON
- FREE data only (Yahoo); no broker gateways; clean-env execution everywhere
- Money-path functions (`grade_factor_loading`, `merge_concentration_grade`,
  `generate_tweaks`) are frontier-reviewed — see `research_2026-08_ns5_v1_roadmap.md`

## Roadmap status

| Phase | Status |
|---|---|
| 1 — Factor pipeline + regression + environment | ✅ `9483780` |
| 2 — Θ + factor-loading grading + API | ✅ `11810a9` |
| 3.1–3.4 — Sector / effective-N / tail-correlation | ✅ `2ab6aa2` |
| 3.5 — Composite merger | ✅ `eed8bcd` |
| 4 — Tweak list + tests + acceptance gate + docs | ✅ `ce029f5` (this doc) |
| 5 — Deployment (QA launchd + smoke) | pending |
