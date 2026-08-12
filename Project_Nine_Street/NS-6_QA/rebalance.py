"""
rebalance.py — Funding path generation for the scenario engine.

FRONTIER SPECIFICATION (junior implements this exact algorithm).

────────────────────────────────────────────────────────────────────────
FUNDING PATH ALGORITHM
────────────────────────────────────────────────────────────────────────

generate_funding_paths(current_weights, target_weights, nav,
                       tax_lot_data, screener_scores, ns2_regimes, theta)
    → list[FundingPath]

  current_weights : dict[ticker → float]          — fraction of NAV
  target_weights  : dict[ticker → float]          — from NS-5 frontier
  nav             : float                         — total portfolio value
  tax_lot_data    : dict[ticker → list[lot]]      — from NS-5 tax axis
  screener_scores : dict[ticker → int]            — agreement 0-4 from A_T
  ns2_regimes     : dict[ticker → (regime, conf)] — from NS-2


  STEP 1 — Identify changes:

    removals = {t: w for t, w in current_weights.items()
                if t not in target_weights}
    adds     = {t: w for t, w in target_weights.items()
                if t not in current_weights}
    existing = {t: (current_weights[t], target_weights[t])
                for t in current_weights if t in target_weights}

    removal_proceeds = sum(w * nav for w in removals.values())
    add_cost         = sum(w * nav for w in adds.values())


  STEP 2 — Generate 4 paths (some may be N/A if conditions not met):

  PATH A — Fund adds from removes ONLY:
    If removal_proceeds >= add_cost:
      Fund each add proportional to its weight from removal pool.
      No existing positions touched.
      1 trade per add + 1 trade per removal.
    If removal_proceeds < add_cost:
      Fund as much as possible from removals, note shortfall.
      This is a partial Path A — remaining shortfall shown as uncovered.

  PATH B — Fund from overweight positions:
    shortfall = max(0, add_cost - removal_proceeds)
    If no removals: shortfall = add_cost
    Rank existing positions by (current - target) / target DESCENDING.
    Skip positions within rebalancing band: |current - target| / target
      <= theta["rebalancing"]["band_rel"].
    Trim largest overweight first until shortfall ≤ 0.
    If shortfall can't be fully covered: mark path as "partial".

  PATH C — Fund via cash reserve (BIL):
    If current_weights.get("BIL", 0) >= theta["rebalancing"]["cash_reserve_min_pct"]:
      Fund adds from BIL reduction.
      Only shown as an option if BIL > min reserve.
      Trade count: 1 trade per add + 1 BIL trade.

  PATH D — Remove lowest-conviction position:
    Score each existing position: screener_scores.get(t, 0) * ns2_confidence
      where ns2_confidence = ns2_regimes[t][1] if t in ns2_regimes else 0.5
    Rank by score ASCENDING.
    Remove the lowest-ranked position.
    If proceeds ≥ add_cost: fund adds from removal + redistribution.
    If proceeds < add_cost: note partial coverage.


  STEP 3 — For each valid path, compute metadata:

    trade_count : int           — number of buy + sell actions
    tax_cost    : float         — realized gains × applicable rate (stub in
                                  Phase 1, real in Phase 3 via tax_context.py)
    risk_impact : dict          — projected changes (stub in Phase 1):
      {"sharpe_delta": float, "effective_n_delta": int,
       "sector_concentration": {sector: delta_pct}, "qqq_corr_delta": float}


  STEP 4 — Rank paths:

    Sort by: trade_count ASC → tax_cost ASC → risk_impact.sharpe_delta DESC
    (Use theta["rebalancing"]["ranking_order"] for priority order.)

    Return at most theta["rebalancing"]["max_paths"] paths.
    Each path carries: {name, trades: [{ticker, action, shares, weight_delta,
                        reason}], trade_count, tax_cost, risk_impact, partial}


  GUARDS:
    - Trades smaller than theta["rebalancing"]["min_trade_size_pct"] × nav
      are suppressed (removed from the trade list for that path).
    - If all trades in a path are suppressed, the path is dropped.
    - If no valid paths remain after suppression, return empty list
      (scenario engine surfaces this to the PM).

  EDGE CASES:
    - Empty current portfolio (first trade): only Path C is valid.
    - Single position portfolio: Path B trims the only position.
    - No removals, no overweights: only Path C is valid.
    - All positions within band: only Paths C and D are valid.

────────────────────────────────────────────────────────────────────────
JUNIOR IMPLEMENTATION NOTES
────────────────────────────────────────────────────────────────────────

- Pure function — no API calls, no I/O. All inputs are pre-computed dicts.
- tax_cost is 0.0 in Phase 1 (stub). Real implementation in Phase 3
  via tax_context.compute_funding_tax_cost().
- risk_impact is stubbed in Phase 1. Real implementation uses NS-5
  frontier projections (re-run frontier with proposed weights).
- Screener scores come from A_T /api/screener (agreement field per ticker).
- NS-2 regimes come from NS-2 /api/ticker (regime + confidence per ticker).
- All thresholds from config.py via theta parameter.
"""
