# R2 — Wire the Live Data Pipeline (Design + Plan)

**Doc:** `full_stack_review_v2.md` §13 R2 (connective tissue between selection/construction and enforcement)
**Status:** DESIGN + PLAN — for Hong's review **before** any development work.
**Author:** Hermes Agent (frontier). **Date:** 2026-08-13.

> Work-split: this document is the frontier/spec layer. Junior implements after
> Hong approves. All formulas / threshold changes are frontier-owned; the cheap
> layer only wires, tests, and flags.

---

## 1. Goal

R2 has three plumbing flows that connect the live stack so NS-6 stops running
on hardcoded/mocked values:

| Flow | Review ref | Current hollow state | Target |
|---|---|---|---|
| **R2a** | Price ingestion → enforcement | `_enforcement_status()` reads `store.latest()` which is **never populated** → `current_dd=0.0`, `spy_dd=0.0`. | A daily EOD price feed computes real drawdown + budget, writes the `drawdown_log`, and enforcement surfaces it. |
| **R2b** | NS-5 frontier → drift target | `_drift_target()` falls back to hardcoded `DEFAULT_WEIGHTS`. **⚠️ Partially done** — see §3 decision. | Drift compares against a real target (policy / frontier), not a magic list. |
| **R2c** | Screener verdict → scenario | `screener_scores` is an optional param the dashboard **never sends**. | "Should I add X?" shows X's real A_T screener agreement. |

---

## 2. Current state (verified in code, 2026-08-13)

**NS-6 side (`NS-6_QA/`):**
- `budget.py` already has the **pure math ready**: `compute_drawdown(price_history)`,
  `compute_spy_drawdown(spy_prices)`, `compute_budget(spy_dd, theta)`,
  `budget_remaining(current_dd, budget, theta)`, and a convenience
  `status_snapshot(portfolio_prices, spy_prices, theta)` that returns the full
  `{current_drawdown_pct, spy_drawdown_pct, budget_pct, budget_remaining_pct}`
  block. **R2a needs only a feed + a scheduled job + thin wiring — the math exists.**
- `store.py` has `drawdown_log` (date PK, `spy_dd_pct`, `portfolio_dd_pct`,
  `budget_pct`, `budget_remaining_pct`, `multiplier`) + `upsert_drawdown()`,
  `latest()`, `query_window()`. **The persistence layer exists and is empty.**
- `qa_server._enforcement_status()` **already reads `store.latest()`** and
  renders `current_drawdown_pct` / `spy_drawdown_pct` / `budget_remaining_pct`
  from it. So once a job writes real rows, enforcement is live with **no
  logic change** — only a "Phase 1 / Phase 2" note update.
- `qa_server._drift_target()` resolves `portfolio_source → PORTFOLIO_POLICIES →
  NS-5 policies.json` with `DEFAULT_WEIGHTS` fallback. Already refactored
  2026-08-13 to use the selected portfolio's policy.
- Scenario handlers (`_scenario_add/remove/replace`) already accept optional
  `screener_scores`, `prices`, `ns2_regimes` and pass them to `scenario_mod`.
- `ns6_dashboard.html` scenario JS builds the body **without** `screener_scores`
  or `prices`.

**Upstream sources:**
- **A_T screener**: `GET /api/fundamentals/screen` (port **9099 QA / 9098 PROD**)
  → `{count, results:[{ticker, agreement, graham, greenblatt, lynch, buffett, ...}]}`.
  `?ticker=X` filters to one. **Read-only — no A_T server change needed (R2c).**
- **NS-5**: `data/policies.json` (policy weights per name, values may be JSON
  strings), `data/portfolios.json` (v2 `{ticker:{shares,...}}`), `data/sleeve_blend.json`.
  NS-5 `frontier.compute_frontier()` returns the frontier **curve** + GMV point,
  **not** a weight vector — GMV/tangency weights need closed-form
  `inv(Σ)·1/(1ᵀinv(Σ)·1)` / `inv(Σ)·μ` (the same reason NS-6's harness inlines
  Ledoit-Wolf). **No `frontier.json` is currently persisted.**

---

## 3. Design

### R2a — Price ingestion → enforcement

**New module:** `NS-6_QA/price_feed.py` (stdlib + yfinance; no new deps).
**New script:** `NS-6_QA/run_daily_price_feed.py` (launchd cron, mirrors NS-5's
`run_weekly_refresh.py` pattern).

**Flow (daily EOD, ~09:00 ET before enforcement):**
1. Resolve current holdings from the cockpit's selected source — reuse
   `qa_server._portfolio_holdings()` logic → `(source, is_model, weights, shares)`.
2. Fetch daily closes (yfinance, tz-naive) for: all holding tickers, **SPY**,
   and **^VIX** (for R3's fast de-risk, stored even if not yet enforced).
   Merge-only-missing into a cache `data/ns6_prices.pkl` (matches backtest
   pattern) so the job is idempotent and offline-reproducible.
3. **Compute portfolio drawdown** two ways, whichever the source supports:
   - *Shares* (NS-5 portfolio): `nav_t = Σ shares_i × price_{i,t}` → `compute_drawdown(nav_series)`.
   - *Weights* (model): `r_t = Σ w_i × r_{i,t}` → chain to NAV → drawdown.
4. `spy_dd = budget.compute_spy_drawdown(spy_closes)`; `budget = budget.compute_budget(spy_dd, theta)`.
5. `remaining = budget.budget_remaining(current_dd, budget, theta)`.
6. `store.upsert_drawdown(date, spy_dd, current_dd, budget, remaining, multiplier)`.
7. Also `store.log_circuit_breaker(...)` / position-stop rows when a hard floor
   triggers (wired later under R3; **out of scope for R2a core**).

**Wiring (thin):** `_enforcement_status()` already reads `store.latest()`. Only
changes: (a) update the `"phase"`/`"note"` fields to reflect live data; (b) add
`"data_as_of"` (the latest row's date) + a `"data_stale_days"` staleness flag so
the PM can see if the feed is fresh. Fail-open: if the feed never ran / row is
stale > N days, surface `"data_stale": true` rather than silently showing 0.0
(sibling of the "fake valid default" bug class).

**launchd:** `com.ninestreet.ns6.pricefeed` plist (mirrors ns5.refresh / ns2
walkforward). QA + PROD both. `env -u PYTHONPATH`, repo-root bootstrap already
in qa_server.

**Sign-bug guard (from skill):** all drawdown/budget values are NEGATIVE
fractions. `status_snapshot` handles this correctly; verify with a real breach
AND a real non-breach test (a "clean" case alone passes silently).

### R2b — NS-5 frontier → drift target  ⚠️ DECISION NEEDED (see §4)

**What exists already:** drift target = the selected portfolio's policy from
`policies.json` (PM decision 2026-08-13, option 2). `DEFAULT_WEIGHTS` is now
only a fallback. So the "hand-tuned magic list" gap is *substantially closed*.

**What the review's diagram asks for:** persist NS-5's **frontier** target
weights (`frontier.json`) and use *those* as the drift target.

**Recommended (frontier position):** keep the **policy target** as the primary
drift reference (it is PM-intentional and already shipped), and treat the
frontier.json persistence as a **separate, optional** construction-layer output
that NS-6 *could* read for a frontier-based target. Do **not** silently replace
the PM-chosen policy target. **Confirm which of these Hong wants before I
finalize §6 tasks.** Options in §4.

### R2c — Screener verdict → scenario

**Dashboard-side (no A_T server change):** in `runScenario(kind)`, before POSTing
`/api/scenario/*`, fetch the A_T screener verdict for the ticker(s) and attach
`screener_scores = {TICKER: agreement}`.

- Add `A_T_SCREENER_URL` to the dashboard (QA `http://localhost:9099/api/fundamentals/screen`,
  PROD `:9098` — env-aware like the service).
- On add/replace: `GET {url}?ticker={add}` → `agreement`; on replace also the
  removed ticker if the PM wants a comparison. Handle 404/empty → omit the key
  (scenario still runs, fail-open).
- **Render it**: `scenario_mod.analyze_add` already consumes `screener_scores`;
  surface the fetched agreement in `renderScenario()` ("Screener agreement: 3/4
  (Graham+Greenblatt+Lynch)") so the PM *sees* it — a silent fetch is a no-op
  (sibling of the consumer-theta / "switch invisible" bug class).
- Also populate `prices` for the ticker so price-sensitivity is real (optional
  stretch within R2c; flag for approval).

**Server-side:** scenario handlers already accept `screener_scores`. No change
unless we want the *server* to fetch (see §4 decision — recommend dashboard
fetch to match the review's "dashboard-to-server, not server-to-server" note).

---

## 4. Open decisions (Hong must confirm — RULE #1, no assumptions)

1. **R2b target source.** The drift TARGET should be:
   - **(A) Selected portfolio's policy (current, already shipped)** — keep,
     treat R2b as "verify + document + test" only. *Lowest risk, respects the
     2026-08-13 PM choice.* **Recommended.**
   - **(B) NS-5 frontier weights (`frontier.json`)** — persist GMV/tangency
     weights for the joint universe and drift against those. Matches the review
     diagram literally, but (i) requires a **target point** decision (GMV vs
     tangency vs profile-mapped — that's R4), (ii) is a *construction* target
     not a *policy*, arguably a different concept, (iii) more work + more risk.
   - **(C) Both**: policy is the reference; frontier is surfaced alongside.

2. **R2c server-vs-dashboard fetch.** Review note says "dashboard-to-server,
   not server-to-server." Recommend dashboard fetch. Confirm no objection.

3. **R2a feed time & staleness window.** Propose ~09:00 ET daily, `data_stale`
   flag after 2 trading days without a fresh row. Confirm.

---

## 5. Execution order (within R2)

Mirror the review's "R2a → R2b → R2c" order — R2a is the core deliverable
(the one that makes enforcement non-decorative).

1. **R2a** — price_feed.py + daily job + enforcement staleness surfacing + tests.
2. **R2b** — resolve §4.1; either document/test current behavior or build
   frontier.json persistence + read.
3. **R2c** — dashboard screener fetch + render + tests.

Each step: TDD (failing test → implement → pass), commit to `feature/*`
(non-negotiable — never master/main), then QA deploy → user verification →
PROD only after approval.

---

## 6. Files likely to change

**R2a**
- `NS-6_QA/price_feed.py` (new) — fetch, portfolio NAV/drawdown, upsert.
- `NS-6_QA/run_daily_price_feed.py` (new) — launchd entrypoint.
- `NS-6_QA/qa_server.py` — `_enforcement_status()` staleness/`data_as_of`/note.
- `NS-6_QA/config.py` — `price_feed` theta (staleness_days, lookback, ^VIX flag).
- `NS-6_QA/tests/test_price_feed.py` (new), `test_qa_server_enforcement.py` (extend).
- `com.ninestreet.ns6.pricefeed.plist` (QA + PROD) + `deploy_prod.sh` entry.
- `NS-6_PROD/` mirror on deploy.

**R2b** (depends on §4.1)
- Option A: `tests/test_drift_target.py` (already hermetic — extend to assert
  policy target used, DEFAULT_WEIGHTS only as fallback), doc only.
- Option B: `NS-5_QA/frontier_persist` output writer + `NS-6_QA/qa_server._drift_target()`
  frontier read + config pairing; deploy both sides.

**R2c**
- `NS-6_QA/ns6_dashboard.html` — scenario JS fetch screener + attach + render.
- `NS-6_QA/tests/test_scenario.py` — assert `screener_scores` flows through.

---

## 7. Tests / validation

- **R2a**: hermetic tests monkeypatch yfinance + DB_PATH (temp). Assert (a) real
  breach → correct negative drawdown/budget; (b) real non-breach → budget intact;
  (c) stale feed → `data_stale:true` (never silently 0.0); (d) shares→weights
  normalization yields sane NAV/drawdown.
- **R2b**: hermetic `_drift_target()` test asserting policy target chosen, and
  DEFAULT_WEIGHTS fallback only when policy missing (already exists — extend).
- **R2c**: hermetic scenario test asserting `screener_scores` flows to the
  analysis; browser check of dashboard after a scenario run (agreement visible).
- **Regression**: `env -i HOME=$HOME py3.9 -m pytest NS-6_QA/tests/ -q` green.

---

## 8. Risks / tradeoffs

- **R2a** risk is low (math + persistence exist); main pitfall = the sign-bug
  class and shares-vs-weights units (both guarded, see skill). yfinance is
  already in the stack.
- **R2b** risk is the **scope decision** — replacing the PM-chosen policy target
  with a frontier target without approval would be an over-reach. Hence §4.
- **R2c** is lowest risk (read-only A_T, optional param already wired); main
  trap = silent fetch with no visible render (guard against).
- **Cross-env path pitfall (deploy):** PROD copy must read `NS-5_PROD/data/...`,
  not `NS-5_QA` (verified pitfall in skill). Applies to R2b if Option B.

---

## 9. Out of scope

- **R3** (fast de-risk wiring) — separate follow-on; R2a provides the VIX level
  R3 needs, but the swap is its own change.
- **R4** (frontier point) / **R5** (benchmark) — PM decisions, no code here.
- **Circuit breakers / position stops live** — stay stubbed; only the *data*
  they need is now populated.
