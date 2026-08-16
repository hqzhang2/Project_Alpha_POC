# NS-PC — Portfolio Constructor / Executor (the missing write path)

**Status:** Design Proposal — PM direction (2026-08-16): spec first, build second.
**Problem:** the stack computes targets (NS-X strategy weights, NS-5 sleeve blend,
NS-8 tactical book, NS-6 enforcement) but **nothing writes `paper_portfolio.json`**.
The live paper book is stuck at 2026-07-24 on the retired NS-1 ETF strategy.
NS-PC closes the loop: it turns "we have all the signals" into "the portfolio is
a concrete, tracked, scheduled number."

---

## 1. Why this layer exists (the gap)

```
NS-7 momentum ──┐
A_T value ──────┼─> NS-5 sleeve_blend (17:45 daily) ──> sleeve_blend.json   ✅ LIVE
NS-8 tactical ──┘
NS-X strategy alloc ────────────────────────────────> strategy_alloc.json   ✅ LIVE
NS-6 enforcement ───────────────────────────────────> floors/funding paths  ✅ LIVE
                        │
                        ▼
              paper_portfolio.json   ❌ NOBODY WRITES THIS (7/24, NS-1 book)
```

Every upstream service emits *targets*. NS-PC is the **deterministic executor**
that assembles targets into positions and writes the portfolio. It is the only
new component needed to make the fund *operational* — everything above it exists.

---

## 2. What NS-PC does

One run, three steps:

1. **Read** three inputs (all on-disk, decoupled — house pattern):
   - `NS-X_QA/data/strategy_alloc.json` → `strategies` = {strategy_id: weight}
   - `NS-5_QA/data/sleeve_blend.json` → `blended` = {ticker: weight} (the
     momentum+value joint equity book, already blended 50/50)
   - `NS-8_QA/data/signals.json` → `weights` = {ETF: weight} (tactical book)
2. **Compose** the fund book per NS-X §6.2:
   ```
   fund_book = Σ (w_strategy × strategy.target_book)
   ```
   with the strategy→book mapping:
   - `ns7` + `at_val` → the equity sleeve (`sleeve_blend.blended`)
   - `ns8` → the tactical book (`signals.weights`)
   - `cash` → SHV (cash proxy) at weight `w_cash`
3. **Guard + materialize** → per-name cap, sector cap, effective-N (NS-X §6.3),
   then convert weights → **whole-share positions** at last close, write
   `paper_portfolio.json` with NAV + history.

---

## 3. Composition rules (the exact mapping)

### 3.1 Strategy → book mapping

| NS-X strategy | role | Target book source | Note |
|---|---|---|---|
| `ns7` (momentum) | return | `sleeve_blend.blended` | equity sleeve (momentum half) |
| `at_val` (value) | defensive | `sleeve_blend.blended` | equity sleeve (value half) |
| `ns8` (tactical) | diversifier | `signals.weights` | 6-ETF tactical book |
| `cash` | riskoff | SHV | residual cash proxy |

**Key subtlety (must get right):** `ns7` and `at_val` are **separate NS-X
strategies but share one target book** (`sleeve_blend.blended`, which NS-5
already merged 50/50). So the **equity allocation** = `w_ns7 + w_at_val`, applied
to the single blended equity book — NOT two separate books double-counted.

### 3.2 The composition formula

```
equity_w = w_ns7 + w_at_val          # total equity sleeve weight
tactical_w = w_ns8                   # NS-8 tactical weight
cash_w = w_cash                      # residual risk-off

fund_book = equity_w × blended(ticker)      # for each ticker in sleeve_blend.blended
          + tactical_w × signals(ticker)     # for each ETF in signals.weights
          + cash_w × SHV

then: renormalize, apply composed-book guards, convert to shares.
```

`fund_book` sums to 1.0 (long-only, no leverage). Weights are **targets** — the
actual share count is derived at last close (§5).

### 3.3 Composed-book guards (NS-X §6.3, now enforced here)

After composition, before materialization:

| Guard | Threshold | Source |
|---|---|---|
| Per-name cap | ≤ 8% (`COMPOSED_MAX_NAME_W`) | NS-X config |
| Sector/β cap | ≤ 40% (`COMPOSED_MAX_SECTOR_W`) | NS-X config |
| Effective-N floor | ≥ 15 (`COMPOSED_MIN_EFF_N`) | NS-X config |

These catch the overlap NS-X's strategy-level 0.40 cap misses: e.g. NS-8's SPY
contains the same large-cap names as NS-7 momentum, and KLAC/AMAT appear in BOTH
the growth and value sleeves (double-weight if not guarded).

---

## 4. Materialization (weights → positions)

- **Price source:** last close for each ticker (yfinance via the same path NS-8
  uses; fallback to the ticker's last `current_price` in the existing portfolio).
- **Whole shares** (no fractional, matching the current file): `shares = floor(nav × w / price)`.
- **Cash residual:** the rounding dust + `cash_w` → `account.cash` (not a position;
  note: the current file uses BIL as cash proxy — NS-PC uses SHV, matching NS-8).
- **NAV:** `account.initial_balance` is preserved from the prior file (or 100000
  if first run); `total_nav` = mark-to-market of positions + cash.
- **History:** append `{date, nav, note}` each run — the note records regime,
  VIX, and the equity/tactical/cash split, so the scoreboard is auditable.

---

## 5. Portfolio schema (writes `scripts/paper_portfolio.json`)

Mirror the existing file's shape so NS-1's read-only dashboard and NS-6 don't
break on the schema:

```json
{
  "account": {
    "initial_balance": 100000.0,
    "cash": 1234.56,
    "total_nav": 100000.0,
    "commissions_paid": 0.0,
    "last_updated": "2026-08-16"
  },
  "positions": {
    "equities": {
      "DELL": {"shares": 20, "entry_price": 123.4, "current_price": 123.4,
               "allocation_pct": 2.5, "strategy": "NS-X-fund", "pnl": 0.0, "pnl_pct": 0.0}
    },
    "options": {}
  },
  "history": [
    {"date": "2026-08-16", "nav": 100000.0, "note": "defensive regime; equity 50% tactical 33% cash 17%"}
  ]
}
```

`strategy` field becomes `"NS-X-fund"` (retiring `"NS-Capital-Preservation"`).

---

## 6. Scheduling & the close-the-loop

- **Trigger:** runs **after** NS-5's 17:45 blend (schedule 17:50, or 18:00 to be
  safe after the blend + NS-8 refresh). launchd `com.ninestreet.portfolio.construct`.
- **Idempotent:** same inputs → same output; safe to re-run.
- **Fail-open:** if any input file is missing/stale, NS-PC **does not write** the
  portfolio — it logs and exits non-zero, leaving the last good book in place
  (never clobbers a valid portfolio with an empty one). This matches NS-8 §7.2.
- **NS-6 read:** NS-6 already reads live prices; after NS-PC writes, its drawdown
  enforcement acts on the *real* fund book instead of the stale NS-1 book.

---

## 7. Retiring NS-1

NS-PC **replaces** the NS-1 book (the `2026-07-24` SPY/XLF/XLV/BIL positions).
NS-1's read-only dashboard keeps working (it just displays the new book), but the
`"NS-Capital-Preservation"` strategy is retired per v3 §4.4 (superseded by NS-6).
No new NS-1 rebalancer is built — that would be resurrecting a retired strategy.

---

## 8. What NS-PC does NOT do

| Exclusion | Who owns it |
|---|---|
| Not a selector | NS-7/A_T pick tickers |
| Not an allocator | NS-X picks strategy weights |
| Not a tactical engine | NS-8 picks asset-class rotation |
| Not a drawdown engine | NS-6 enforces floors |
| Not a live-trading bridge | NS-PC writes the *paper* book only (no broker) |

NS-PC is strictly the **constructor**: targets → positions → file. One job, no
signal logic, no optimization, no broker I/O.

---

## 9. Directory & implementation

```
NS-PC/                          # or scripts/portfolio_constructor.py — TBD
├── constructor.py     # read inputs → compose → guard → materialize → write
├── config.py          # thresholds (reuse NS-X composed-book guards)
├── qa_server.py       # :9301 (QA) / :9300 (PROD) — GET /portfolio, POST /construct
└── tests/
```

Ports: **QA 9301 · PROD 9300** (next free pair after NS-X 9291/9290).

---

## 10. Acceptance gate

| Metric | Threshold |
|---|---|
| Weights sum | = 1.0 (±1e-6) after composition |
| Long-only | all w ≥ 0, no shorts |
| Per-name cap | ≤ 8% post-composition |
| Effective-N | ≥ 15 post-composition |
| Deterministic | same inputs → same portfolio |
| Fail-open | missing/stale input → no write, exit non-zero |
| Schema-compatible | NS-1 dashboard + NS-6 read the new file unchanged |

---

## 11. Decision

**Proceed: spec (this doc) → build (`constructor.py` + config + tests + QA server
:9301).** Skip the v4 strategy-data store for now (NS-PC reads the existing
on-disk files; the store is a later optimization, not a blocker).

**Decisions (PM, 2026-08-16):**
1. **Whole shares** (no fractional) — cash absorbs rounding, matches the current
   `paper_portfolio.json` convention.
2. **Cash proxy = BIL** (not SHV) — `account.cash` holds uninvested residual;
   BIL is the cash-equivalent *position* when the cash allocation is material
   (preserves the current file's BIL-as-cash-equivalent convention).
3. **New `NS-PC/` directory** — a proper service (portal tab + launchd), like NS-X.

### 11.1 Cash-proxy semantics (BIL)

- The `cash` NS-X strategy weight maps to **BIL** as a position (cash equivalent),
  consistent with the current book's "BIL as cash equivalent (7%)".
- Rounding dust from whole-share materialization goes to `account.cash` (uninvested
  residual), NOT a BIL position — dust is de-minimis, BIL is the *intentional* cash
  allocation.
- So: `w_cash` → BIL position; whole-share rounding → `account.cash`.
