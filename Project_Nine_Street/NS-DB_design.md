# NS-DB — Centralized Strategy Data Store (PostgreSQL)

**Status:** DESIGN + IMPLEMENTATION DOC (approved direction, awaiting sign-off to build)
**Date:** August 2026
**Decisions locked (PM, 2026-08-16):**
1. **PostgreSQL** (existing homebrew `postgresql@18`, DB `project_alpha`)
2. **Shared** DB across QA/PROD (one `project_alpha`; the paper book is one book)
3. **Phased** migration (details below)
4. **JSONB** for `strategy_output.payload`

**Source of truth for:** every shared datum across NS services and A_T, plus
all portfolio state (paper book, named portfolios incl. "Hyperscaler").

---

## PART A — DESIGN

### A.1 The Decision

**PostgreSQL**, consolidated onto the *existing* `project_alpha` DB (port 5432,
homebrew `postgresql@18`). A_T is already on it (`daily_prices` 16,913 rows,
`financials_income/balance/cashflow`, `financial_tickers`, `financial_watchlist`,
`portfolio_v1_performance`). NS is the outlier — per-service SQLite
(`ns6.db`/`ns7.db`/`ns8.db`) + ~15 JSON files.

"Centralize" = **migrate NS onto `project_alpha`**. No second DB. No SQLite.

### A.2 Current landscape → target mapping

| Today (NS, fragmented) | Target (Postgres `project_alpha`) |
|---|---|
| `scripts/paper_portfolio.json` | `portfolios` + `positions` + `portfolio_guardrails` + `portfolio_nav` |
| `NS-5_*/data/portfolios.json` (incl. **Hyperscaler**) | `portfolios` (kind=`policy`) |
| `NS-5_*/data/sleeve_blend.json` | `strategy_output` (service=`ns5`, kind=`blend`) |
| `NS-7_*/data/selection.json` | `strategy_output` (service=`ns7`, kind=`selection`) |
| `NS-8_*/data/signals.json` | `strategy_output` (service=`ns8`, kind=`signals`) |
| `NS-X_*/data/strategy_alloc.json` | `strategy_output` (service=`nsx`, kind=`alloc`) |
| `NS-X_*/data/strategy_streams.json` | `strategy_returns` |
| `NS-8_*/data/ns8_hist_closes.json` + `NS-7_*/data/bench_closes.json` | `daily_prices` (extend) |
| `common/data/regime_history.db` | `regime_history` |
| `NS-6_*/data/ns6.db` | `drawdown_log` / `circuit_breaker_log` / `performance_log` / `settings` |
| `NS-7_*/data/ns7.db` | `selection`/`league`/`volume`/`refresh_meta` (fold into strategy_output where they're cross-service; local scratch can stay) |
| `NS-8_*/data/ns8.db` | `signals`/`audit_log`/`tranche_state` (fold; audit_log is append-only) |

### A.3 Schema

```sql
-- ── Portfolio (replaces paper_portfolio.json + portfolios.json) ─────────
CREATE TABLE portfolios (
  id            SERIAL PRIMARY KEY,
  name          TEXT NOT NULL UNIQUE,        -- 'paper' (live), 'hyperscaler', ...
  kind          TEXT NOT NULL,               -- 'live' | 'policy' | 'backtest'
  initial_balance NUMERIC(14,2) NOT NULL DEFAULT 100000.00,
  cash          NUMERIC(14,2) NOT NULL DEFAULT 0,
  total_nav     NUMERIC(14,2) NOT NULL DEFAULT 0,
  commissions   NUMERIC(14,2) NOT NULL DEFAULT 0,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE positions (
  id             SERIAL PRIMARY KEY,
  portfolio_id   INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
  ticker         VARCHAR(16) NOT NULL,
  shares         NUMERIC(14,4) NOT NULL,
  entry_price    NUMERIC(14,4) NOT NULL,
  current_price  NUMERIC(14,4),
  allocation_pct NUMERIC(6,3),
  strategy       VARCHAR(64),                -- 'NS-X-fund'
  pnl            NUMERIC(14,2) DEFAULT 0,
  pnl_pct        NUMERIC(10,6) DEFAULT 0,
  as_of          DATE NOT NULL,
  UNIQUE (portfolio_id, ticker, as_of)
);

CREATE TABLE portfolio_guardrails (
  portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
  as_of        DATE NOT NULL,
  n            INTEGER,
  eff_n        NUMERIC(8,3),
  max_weight   NUMERIC(8,4),
  weights_sum  NUMERIC(8,6),
  PRIMARY KEY (portfolio_id, as_of)
);

CREATE TABLE portfolio_nav (
  portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
  date         DATE NOT NULL,
  nav          NUMERIC(14,2) NOT NULL,
  note         TEXT,
  PRIMARY KEY (portfolio_id, date)
);

-- ── Strategy output (replaces selection/signals/blend/alloc JSONs) ──────
CREATE TABLE strategy_output (
  service      VARCHAR(16) NOT NULL,         -- 'ns5','ns7','ns8','nsx','nspc'
  kind         VARCHAR(16) NOT NULL,         -- 'blend','selection','signals','alloc'
  as_of        DATE NOT NULL,
  payload      JSONB NOT NULL,
  generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (service, kind, as_of)
);

-- ── Per-strategy return stream (replaces strategy_streams.json) ─────────
CREATE TABLE IF NOT EXISTS strategy_returns (
    strategy_id VARCHAR(16) NOT NULL,          -- 'ns7','at_val','ns8','cash'
    date        DATE NOT NULL,
    return      DOUBLE PRECISION NOT NULL,     -- computed float (round-trip exact)
    source      VARCHAR(32),                   -- 'live','walkforward','reference'
    PRIMARY KEY (strategy_id, date)
);

-- ── Regime (from common/regime_history.db) ──────────────────────────────
CREATE TABLE regime_history (
  date        DATE PRIMARY KEY,
  regime      VARCHAR(16) NOT NULL,
  growth_prob NUMERIC(6,4),
  source      VARCHAR(32)
);

-- ── NS-6 enforcement logs (from ns6.db) ─────────────────────────────────
CREATE TABLE drawdown_log ( id SERIAL PRIMARY KEY, date DATE, current_dd NUMERIC(8,6), note TEXT );
CREATE TABLE circuit_breaker_log ( id SERIAL PRIMARY KEY, date DATE, tripped BOOLEAN, reason TEXT );
CREATE TABLE performance_log ( id SERIAL PRIMARY KEY, date DATE, nav NUMERIC(14,2), return NUMERIC(12,8) );
```

`daily_prices` already exists; NS closes become inserts into it (no DDL change
needed beyond confirming the columns cover NS's needs — they do: ticker/date/
raw_close/adj_close/volume).

### A.4 Access pattern — `common/db.py`

One shared module. Every NS service imports typed accessors; no service opens a
raw connection or hardcodes a path.

```python
# common/db.py
CONN = "dbname=project_alpha user=chuck host=localhost"   # or env DATABASE_URL

def get_portfolio(name) -> dict             # replaces reading paper_portfolio.json
def write_portfolio(name, doc) -> None      # replaces writing it
def latest_strategy_output(service, kind)   # replaces reading signals/selection/blend/alloc json
def write_strategy_output(service, kind, payload)
def strategy_returns(strategy_id) -> list   # replaces reading strategy_streams.json
def write_strategy_returns(strategy_id, rows)
def append_nav(portfolio, date, nav, note)  # replaces appending to the history array
```

Connection config via `DATABASE_URL` env var, falling back to the localhost DSN.
`common/db.py` is the **single seam**; migration phases touch only this module
plus the service call-sites.

---

## PART B — IMPLEMENTATION (phased)

### Phase 0 — Prerequisites (no schema, no behavior change)

- [ ] Confirm `psycopg2` importable in each runtime that needs DB access
      (hermes venv ✓; **CLT py3.9 — must verify `psycopg2` is installed**, the
      NS-5/NS-6/NS-7/NS-8/NS-X services run on py3.9).
- [ ] Create the tables via a single idempotent `schema.sql` + `common/db.py`
      `ensure_schema()` (runs `CREATE TABLE IF NOT EXISTS` on import or a
      `--init` flag).
- [ ] Add a `/health` DB ping to the portal (one row: Postgres reachable).

**Acceptance:** `psql -c "\dt"` shows the new tables; portal health reports
Postgres OK; no service behavior changed.

### Phase 1 — Backfill portfolios + strategy data (read-only, additive)

- [ ] Backfill `portfolios`/`positions`/`portfolio_nav`/`portfolio_guardrails`
      from the current `paper_portfolio.json` (one `paper` portfolio) and from
      `NS-5_*/data/portfolios.json` (Hyperscaler + others as `policy`).
- [ ] Backfill `strategy_output` from the latest `sleeve_blend.json`,
      `selection.json`, `signals.json`, `strategy_alloc.json`.
- [ ] Backfill `strategy_returns` from `strategy_streams.json`.
- [ ] Backfill `regime_history` from `regime_history.db`.
- [ ] **Do NOT delete the JSON files yet** — readers still use them.

**Acceptance:** Postgres rows == file contents (spot-checked); old files still
authoritative and still read by services (no regression).

### Phase 2 — Rewire the write path (NS-PC → Postgres)

- [ ] `NS-PC/constructor.py` (and `_PROD` copy): `write_portfolio()` writes
      `portfolios`+`positions`+`portfolio_nav`+`portfolio_guardrails` instead of
      `paper_portfolio.json`.
- [ ] `run_scheduled.py` (daily 18:00) writes to Postgres.
- [ ] NS-1 dashboard + NS-6 read `get_portfolio('paper')` from Postgres.
- [ ] **Delete `paper_portfolio.json`** (now redundant; already untracked).

**Acceptance:** daily construct updates the `paper` portfolio rows; NS-1/NS-6
render the same book from Postgres; `paper_portfolio.json` gone.

### Phase 3 — Rewire cross-service reads (kill the JSON seam)

- [ ] `NS-X_QA/strategy_data.py`: `strategy_returns()` reads `strategy_returns`
      table (with a backfill-on-miss fallback so a cold PROD DB still works).
- [ ] `NS-PC/config.py`: read `latest_strategy_output('ns5','blend')`,
      `('ns8','signals')` from Postgres instead of the file paths.
- [ ] `NS-X_PROD/config.py`: read `strategy_output`/`strategy_returns` from
      Postgres — **this removes the v4.0.0 deploy seam** (NS-X reading
      QA-generated NS-7 JSONs).
- [ ] Delete the now-orphaned `sleeve_blend.json`/`selection.json`/`signals.json`/
      `strategy_alloc.json`/`strategy_streams.json` readers (keep files until
      PROD verified).

**Acceptance:** NS-X PROD allocates identically reading from Postgres (ns7 40 /
ns8 40 / at_val 10 / cash 10, `streams_differentiated: True`); no JSON seam.

### Phase 4 — Market data + regime + NS-6 logs

- [ ] `daily_prices`: NS-8 6-ETF closes + NS-7 bench closes write to the shared
      table instead of `ns8_hist_closes.json`/`bench_closes.json`.
- [ ] `regime_history`: NS-2/NS-6 read/write the shared table.
- [ ] NS-6 `drawdown_log`/`circuit_breaker_log`/`performance_log` move from
      `ns6.db` to Postgres (append-only — easy).

**Acceptance:** no `*.db` or `*_closes.json` in the NS data paths; all reads
hit Postgres.

### Phase 5 — Deprecate SQLite + JSON, final cleanup

- [ ] Delete `ns6.db`/`ns7.db`/`ns8.db`/`regime_history.db` and all remaining
      JSON data files once every reader is on Postgres.
- [ ] Update `.gitignore` (drop now-unneeded data-dir entries).
- [ ] Update `deploy_prod.sh`/plists if the DB init needs to run at boot.

**Acceptance:** `find Project_Nine_Street -name "*.db"` returns only scratch
(if any); `*.json` data files gone; full suite green; PROD healthy.

---

## PART C — WORK SPLIT (frontier vs junior)

Standing rule: **frontier = methodology/signals/stats + writes specs; junior =
UI/tests/docs/plumbing, never touches signal/backtest functions.** The DB is
*storage*, so most of it is plumbing — but the **data semantics** (what a return
stream *is*, how a portfolio row maps to the book, backfill correctness) are
frontier.

| Task | Owner | Why |
|---|---|---|
| Final schema (tables, types, constraints) | **Frontier** | data-model correctness is methodology-adjacent |
| `common/db.py` module + `ensure_schema()` | **Frontier** | the single seam; semantics of each accessor |
| `schema.sql` idempotent DDL | **Frontier** | pair with db.py |
| Phase 1 backfill scripts (file→DB) | **Frontier** | correctness of the migration (no data loss/drift) |
| Verify backfill: DB == file (diff) | **Frontier** | must not trust the migration blindly |
| NS-PC write path (`write_portfolio`) | **Frontier** (design) + **Junior** (wire the call) | semantics frontier, plumbing junior |
| NS-X `strategy_data.py` → `strategy_returns` | **Frontier** | return-stream semantics is signal-adjacent |
| NS-PC/NS-X `config.py` path swaps | **Junior** | mechanical path→DB swap |
| NS-1/NS-6 dashboard read from Postgres | **Junior** | UI/plumbing |
| Portal `/health` DB ping | **Junior** | UI |
| `run_scheduled.py` DB write | **Junior** | mechanical (frontier owns `write_portfolio`) |
| Phase 4 market data / regime / NS-6 logs | **Junior** (moves) + **Frontier** (verify no data loss) | mostly mechanical append-log moves |
| Phase 5 deprecation + `.gitignore` + deploy wiring | **Junior** | cleanup |
| Tests for `common/db.py` accessors | **Junior** | test scaffolding |
| Tests that backfill == source (correctness) | **Frontier** | data-integrity gate |
| Update NS-DB_design.md as implementation proceeds | **Junior** (doc) | documentation |

**Hard frontier ownership (junior must not touch):** the schema itself, the
backfill correctness, the `strategy_returns`/`strategy_output` semantics, and the
`get_portfolio`/`write_portfolio` mapping. Everything else is plumbing junior can
own.

---

## PART D — Risks & the one open question

| Risk | Mitigation |
|---|---|
| py3.9 runtime lacks `psycopg2` | Phase 0 gate: verify + install if needed before any rewire |
| Backfill drifts from source | Phase 1 diff gate (DB == file) before any reader switches |
| One shared DB → QA and PROD both write `paper` | this is *intended* (one book); NS-PC PROD is the single writer, QA reads |
| Postgres down = stack down | already A_T's dependency; add portal health ping (Phase 0) |
| Cold PROD DB has no `strategy_returns` rows | Phase 3 backfill-on-miss fallback |

**One thing to settle before build (flag now, not a blocker):** the **CLT py3.9
runtime** — NS-5/6/7/8/NS-X launchd jobs run on it — must have `psycopg2`. I'll
verify in Phase 0. If it's absent, options are (a) install `psycopg2` into the
3.9 site-packages, or (b) route DB access through a small stdlib-only `sqlite`…
no — through `psycopg2` only; (c) run those services' DB reads via a helper.
Recommendation: install `psycopg2-binary` into py3.9 (one command), which is a
clean, non-invasive fix. Confirm you're OK with that before I touch it.

---

## PART E — Corrections learned during Phase 0/1 (frontier, 2026-08-16)

**E.1 — psycopg2 is on py3.9, missing on hermes venv (inverted from Part D).**
The CLT py3.9 runtime **already has `psycopg2` 2.9.11** (no install needed — the
A_T financials already depend on it). The **hermes venv (Python 3.11)** does NOT
have it — and that's the runtime **NS-PC** (the portfolio write path) runs on
(needs it for yfinance). So the install target is the **hermes venv**, not py3.9.
`common/db.py` is written to fail-open on both runtimes (import guard + try/
except), so nothing crashes either way — but the NS-PC write path needs
`psycopg2-binary` installed in the hermes venv before Phase 2.

**E.2 — `strategy_returns.return` is `DOUBLE PRECISION`, not `NUMERIC(12,8)`.**
Daily returns are computed floats (Python `float`, numpy), and `NUMERIC(12,8)`
truncated them (e.g. `0.0027965714…` → `0.00279657`), breaking the backfill
diff. `DOUBLE PRECISION` round-trips bit-exact. Applied to `strategy_returns`
and `performance_log`.

**E.3 — `paper_portfolio.json` history has duplicate same-day entries.**
The source file held 5 entries all dated `2026-08-16` (an NS-PC append bug — it
appends rather than upserts by date). The DB PK `(portfolio_id, date)` correctly
collapses to one NAV/day. The DB is *more* correct than the file; the source bug
is in NS-PC's history append, not the migration. Flagged for the junior model to
fix the NS-PC append when rewiring the write path.

**E.4 — Named policy portfolios (Hyperscaler) are target weights, not traded
positions.** `portfolios.json` maps name → {ticker: weight}. The backfill stores
them as `portfolios(kind='policy')` rows (empty positions) AND preserves the
actual definition in `strategy_output(service='ns5', kind='policy_<name>')`.
NS-6's drift target should read the latter.

---

## PART F — Corrections learned during Phase 4 (frontier, 2026-08-16)

**F.1 — NS-6 log schema was drafted too narrow in Phase 0.** The Phase-0 draft
had `drawdown_log(id, date, current_dd, note)` / `performance_log(id, date, nav,
return)` / `circuit_breaker_log(id, date, tripped, reason)` — but the real
`NS-6_QA/store.py` tables carry far richer columns: `drawdown_log(date PK,
spy_dd_pct, portfolio_dd_pct, budget_pct, budget_remaining_pct, multiplier,
vix_level, position_drawdowns JSON, cross_sectional_corr)`, `performance_log(date
PK, nav, ret, spy_ret, universe_ret, contributions JSON)`, `circuit_breaker_log
(id, timestamp, breaker_type, ticker, detail)`, plus a `settings(key, value)`
table the draft omitted. **Schema corrected to mirror `store.py` exactly**, with
the JSON sub-documents (`position_drawdowns`, `contributions`) stored as JSONB.
The accessors (`upsert_drawdown`, `latest_drawdown`, `query_drawdown`,
`upsert_performance`, `query_performance`, `log_circuit_breaker`, `query_breakers`,
`get_setting`, `set_setting`) mirror `store.py`'s API so the junior rewire is a
drop-in swap.

**F.2 — `bench_closes.json` is `[[date, price], …]` pairs, not a bare price
list.** The NS-7 bench closes are self-describing `[date, price]` pairs (the
`spy_closes.json` dates are redundant). The first daily_prices backfill attempt
indexed it as a flat list and wrote the date-array as a numeric (caught by the
`InvalidTextRepresentation` error). Corrected to unpack the pairs.

**F.3 — daily_prices dedup:** NS-8's 6-ETF closes (5,186 dates each, from
2006-01-03) and NS-7's SPY/QQQ bench (3,172 dates, from 2014-01-02) both overlap
A_T's existing `daily_prices` SPY rows (1,301 dates). `ON CONFLICT (ticker, date)`
upserts so the shared table holds the union without duplicates; the longest
series (NS-8, 2006→present) wins for SPY.

---

## PART G — Phase 5 deprecation: scoped by reader-reach (junior, 2026-08-16)

**G.1 — Full file deprecation is DEFERRED, gated on the design's own rule.**
The design (§B Phase 5) deletes sqlite/JSON files "once every reader is on
Postgres." That precondition is NOT met for three readers, which were never
rewired (and `common.db` exposes no accessors for them):

| Reader | Still reads | Status |
|---|---|---|
| **NS-7** (`store.py`, `config.py`) | `ns7.db`, `selection.json`, `bench_closes.json` | sqlite/JSON — not rewired |
| **NS-8** (`store.py`, `config.py`, `pipeline.py`) | `ns8.db`, `signals.json`, `ns8_hist_closes.json` | sqlite/JSON — not rewired |
| **`common/regime_store.py`** (read by NS-2/NS-5/NS-6/NS-7) | `regime_history.db` (sqlite) | sqlite — not rewired |

Deleting `ns7.db`/`ns8.db`/`regime_history.db`/their JSONs now would break these
readers. This is **not** a junior skip — it's the design's own gate. The safe,
complete scope for this phase is below.

**G.2 — What Phase 5 did land (safe, verified).**
- **`.gitignore` already covers every NS `data/` dir** (NS-5/6/7/8/X/PC, QA+PROD)
  and `common/data/` — all `.db` and data JSONs are runtime state, nothing tracked
  to remove. The only tracked `.db` is `sector_etfs.db` (repo root, A_T migration
  source — intentionally kept).
- **NS-6 logs now write to Postgres in prod** (store.py seam → `common.db`),
  with the sqlite implementation retained as the hermetic **test seam** (temp
  `DB_PATH` monkeypatch → `_use_pg()==False`). 247 NS-6 tests stay green.
- `run_daily_price_feed.py` persists via the store seam → Postgres automatically
  (no direct change needed).

**G.3 — Remaining work for FULL deprecation (a separate PR, frontier-scoped).**
Rewire NS-7, NS-8, and `common/regime_store.py` to `common.db` (needs new
accessors: NS-7 selection/volume, NS-8 signals/audit, regime CRUD), then delete
the orphaned sqlite/JSON files. This is intentionally out of this PR's scope.

---

*Design + implementation doc. Phases 0–4 complete + Phase 5 (scoped) complete.
Frontier: schema/accessors/backfill. Junior: NS-PC→PG, NS-X→PG, NS-6 store→PG.
Full file deprecation deferred to a reader-rewire PR (Part G.3).*
