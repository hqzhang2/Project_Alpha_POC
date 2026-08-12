"""
Phase 2 Backtest Upgrade Spec (junior implements)
=================================================
1. MULTIPLIER: replace compute_exposure_multiplier() with v2().
   Needs 4 signals computed from price data (no live services for 2016-2026):

   a. Regime (R1-R4): VIX-based 4-zone proxy:
      VIX < 18     → "R1" (Expansion)
      VIX 18-28    → "R2" (Overheating)
      VIX 28-35    → "R3" (Recession)
      VIX > 35     → "R4" (Stagflation)
      LABEL CLEARLY AS PROXY — not the NS-5 macro regime.

   b. Vol ratio: 60d trailing annualized vol / long-run (full window) vol.
      Compute from portfolio daily returns.

   c. Stock-bond correlation: SPY/TLT 60d rolling correlation.

   d. VIX level + trend: fetch ^VIX from yfinance (add to universe).
      trend = 5d SMA(VIX_today) - SMA(VIX_yesterday) > 0.

2. PUT DRAG: when multiplier < 0.80 AND put recommended, subtract
   put_annual_cost/252 from daily returns as a drag (parametric proxy).

3. COMPARISON: run both P1 and P2 over the SAME window, same universe,
   same screener top_n. Output side-by-side annual table + gate comparison.

4. REPORT:
   | Metric      | Phase 1    | Phase 2   | Delta    |
   | port tot%   | 156.0      | TBD       | TBD      |
   | port max DD | −24.7      | TBD       | TBD      |
   | DD ratio    | 0.73       | TBD       | TBD      |
   | excess pos yrs | 1/11   | TBD       | TBD      |
   | DD halved yrs  | 1/11   | TBD       | TBD      |
   | trades/qtr  | 2.5        | TBD       | TBD      |

5. Acceptance gate (same criteria): excess >= 7/10 yrs, DD <= SPY/2 in >= 8/10 yrs,
   trades < 30/qtr.

Phase 2 EXPECTED IMPROVEMENT:
   - Lower max DD (earlier signal detection via regime/vol/corr/VIX tiers)
   - Shorter DD duration (multiplier reduces exposure BEFORE deep dive)
   - Potentially lower returns (more time in reduced-exposure mode + put drag)
   - Higher Sharpe ratio (less forced selling at bottoms)
"""
