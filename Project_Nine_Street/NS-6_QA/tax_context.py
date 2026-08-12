"""
tax_context.py — Tax-aware funding path ranking (Phase 3).

FRONTIER SPECIFICATION (junior implements this methodology).

────────────────────────────────────────────────────────────────────────
PHASE 3 TAX-AWARE RANKING
────────────────────────────────────────────────────────────────────────

Consumes NS-5 tax axis data (lot-level cost basis, TLH availability,
tax profile) and re-ranks the funding paths from rebalance.py by
after-tax cost. The BEFORE-TAX paths are generated as-is (rebalance.py
owns all path-construction methodology); this module computes the
AFTER-TAX cost per path and optionally re-ranks.

────────────────────────────────────────────────────────────────────────
TAX COST FORMULA
────────────────────────────────────────────────────────────────────────

compute_funding_tax_cost(funding_path, tax_lot_data, tlh_available,
                         tax_profile, theta) → float

  funding_path : dict       — one path dict from rebalance.generate_funding_paths()
  tax_lot_data : dict       — {ticker: {lots: [{date, shares, cost_per_share}]}}
                              from NS-5 tax axis (/api/grade response, tax axis)
  tlh_available : float     — unrealized ST losses available for harvest ($)
  tax_profile   : dict      — {federal_bracket: 0.24, state_rate: 0.05, niit: True}
                              from NS-5 tax axis (same schema as TAX_DEFAULTS)
  theta         : dict      — config.load_theta() (tax rates not used here;
                              rates come from tax_profile)

  COMPUTATION:

  1. For each SELL trade in the path:
     a. Look up ticker in tax_lot_data. If no lot data: assume 0 cost basis
        (worst case — entire proceeds are taxable gain). Flag as unclassified.

     b. Select lots by HIGHEST COST BASIS FIRST (minimise realized gain).
        Iterate lots sorted by cost_per_share DESC, allocate shares to the
        sell until the sell quantity is filled.

     c. Compute gain = (sell_price − cost_per_share) × shares_sold.
        sell_price comes from the path's price context (passed in or
        fetched by caller).

     d. Classify gain: held > 365 days → LTCG, ≤ 365 days → STCG.
        A lot with unknown date → STCG (conservative).

     e. Apply tax rate:
        STCG: marginal_ordinary = federal_bracket + state_rate + (3.8% if NIIT)
        LTCG: marginal_ltcg = 0.20 + state_rate + (3.8% if NIIT)
        (2026 US rates: 20% max LTCG, 0/15/20 brackets simplified to 20%
         for this level — exact bracket is Phase 3+ precision)

  2. Net against TLH available (FIRST DOLLAR OF HARVEST offsets highest-rate
     gains — STCG first, then LTCG).
     Available TLH = max(tlh_available, 0).
     Net tax = max(0, gross_tax − available_tlh).

  3. Returns total tax cost in $ (positive = cost, 0 = fully offset).

────────────────────────────────────────────────────────────────────────
RANKING
────────────────────────────────────────────────────────────────────────

rank_paths_by_after_tax_cost(paths, tax_lot_data, tlh_available,
                             tax_profile, prices, theta) → list[FundingPath]

  Re-sorts the paths by (after_tax_cost ASC → trade_count ASC →
  risk_impact.sharpe_delta DESC).

  Each path gets a new key: "after_tax_cost": float (added to the path dict
  in-place).

────────────────────────────────────────────────────────────────────────
BACKTEST WIRING
────────────────────────────────────────────────────────────────────────

For the Phase 3 backtest, tax_lot_data and tlh_available are phantom
inputs (the backtest doesn't have multi-year lot history). The simplest
honest approach:

  1. TAX COST PROXY: assume a flat tax drag equal to a fixed % of the
     total SELL notional per quarter. E.g. LTCG rate ~15-24% × gain fraction.
     In the absence of lot data, use:
       tax_cost = Σ(sell_notional) × 0.05  (assume ~20% of position is gain,
                                            taxed at ~24% = 4.8% drag)

     This is labeled "TAX PROXY" in the output — not precise, but honest
     about the direction and magnitude.

  2. COVERED CALL PROXY: when covered_call_gate() returns True, add an
     annualized yield boost to daily returns:
       call_yield_annual = 0.04  (4% annual = reasonable 30-45 DTE 0.25Δ
                                  overwrite on SPY-level IV)
       Only applies to days when multiplier ≥ gate_multiplier (0.60).
       Multiplied by overwrite fraction (0.50 or 0.25 based on multiplier).

     Add: daily_port_ret += call_yield_annual / 252 * overwrite_pct

  3. COMPARISON: add a third column to --compare-weighting showing P2+tax.
     Or run --phase 3 which includes tax proxy + call proxy.

────────────────────────────────────────────────────────────────────────
JUNIOR IMPLEMENTATION NOTES
────────────────────────────────────────────────────────────────────────

- compute_funding_tax_cost() is pure math — no API calls.
- Lot selection follows NS-5 tax.py _compute_stcg_lots pattern (highest
  cost basis first).
- Unknown lot date → STCG (conservative, matches NS-5 fail-open principle).
- Tax rates from tax_profile dict (not theta — tax_profile IS the marginal
  rate source, matching NS-5's pattern of computing drags from bracket fields).
- Backtest wiring: tax proxy + call proxy are parametric approximations.
  Label them clearly as proxies in the output. Done correctly in Phase 3
  when live services (NS-5 tax + A_T option chains) are consumed.
"""
