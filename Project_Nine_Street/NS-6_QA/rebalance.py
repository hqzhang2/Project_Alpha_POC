"""
rebalance.py — Funding path generation for the scenario engine.

Implements the frontier 4-path algorithm. Pure function — no API calls,
no I/O. All inputs pre-computed dicts.

tax_cost and risk_impact are stubbed (Phase 1); real implementations land in
Phase 3 (tax_context.py) and via NS-5 frontier projections respectively.
"""

import logging
from typing import Dict, List, Optional

import config

log = logging.getLogger("ns6.rebalance")

Trade = Dict
FundingPath = Dict


def _shares(ticker: str, weight: float, nav: float, price: float) -> int:
    """Approximate shares from weight×nav / price. Round down to whole shares."""
    if not price or price <= 0:
        return 0
    return int(round(weight * nav / price))


def _build_trade(ticker, action, weight_delta, nav, prices, reason,
                 min_trade=None) -> Optional[Trade]:
    """Build a trade dict, or None if below the min-trade-size guard.

    Suppresses trades whose notional (weight×nav) is below min_trade (or
    that round to 0 shares). The spec requires suppressing sub-min trades;
    if ALL trades in a path are suppressed, the path is dropped.
    """
    notional = abs(weight_delta) * nav
    if min_trade is not None and notional < min_trade:
        return None
    shares = _shares(ticker, abs(weight_delta), nav, prices.get(ticker, 0))
    if shares <= 0:
        return None
    return {
        "ticker": ticker,
        "action": action,  # "BUY" | "SELL"
        "shares": shares,
        "weight_delta": round(weight_delta, 4),
        "reason": reason,
    }


def _empty_path_meta(nav: float) -> Dict:
    return {
        "trade_count": 0,
        "tax_cost": 0.0,  # Phase 1 stub — real in Phase 3
        "risk_impact": {  # Phase 1 stub — real via NS-5 frontier projections
            "sharpe_delta": 0.0,
            "effective_n_delta": 0,
            "sector_concentration": {},
            "qqq_corr_delta": 0.0,
        },
        "partial": False,
    }


def generate_funding_paths(current_weights, target_weights, nav,
                           tax_lot_data=None, screener_scores=None,
                           ns2_regimes=None, theta=None, prices=None) -> List[FundingPath]:
    """Generate 3-5 ranked funding paths to move current→target weights.

    Returns list of paths, each: {name, trades, trade_count, tax_cost,
    risk_impact, partial}. Empty list if no valid path survives guards.
    """
    theta = theta or config.load_theta()
    rb = theta["rebalancing"]
    prices = prices or {}
    tax_lot_data = tax_lot_data or {}
    screener_scores = screener_scores or {}
    ns2_regimes = ns2_regimes or {}

    current_weights = current_weights or {}
    target_weights = target_weights or {}

    min_trade = rb["min_trade_size_pct"] * nav
    band = rb["band_rel"]
    max_paths = rb["max_paths"]

    # STEP 1 — identify changes
    removals = {t: w for t, w in current_weights.items() if t not in target_weights}
    adds = {t: w for t, w in target_weights.items() if t not in current_weights}
    existing = {t: (current_weights[t], target_weights[t])
                for t in current_weights if t in target_weights}

    removal_proceeds = sum(w * nav for w in removals.values())
    add_cost = sum(w * nav for w in adds.values())

    def _finish(name, trades, partial=False):
        trades = [t for t in trades if t is not None]
        # Guard: suppress trades below min size (already dropped in _build_trade
        # via None), drop path if no trades remain.
        if not trades:
            return None
        meta = _empty_path_meta(nav)
        meta["name"] = name
        meta["trades"] = trades
        meta["trade_count"] = len(trades)
        meta["partial"] = bool(partial)
        return meta

    paths = []

    # ── PATH A: fund adds from removes only ─────────────────────────────
    if removals and adds:
        trades = []
        # Sell each removal.
        for t, w in removals.items():
            tr = _build_trade(t, "SELL", -w, nav, prices, "removal", min_trade)
            if tr is not None:
                trades.append(tr)
        # Fund each add proportional to its target weight.
        add_w = sum(adds.values())
        partial = removal_proceeds < add_cost
        for t, w in adds.items():
            # distribute removal pool proportionally; cap at target weight
            funded = min(w * nav, removal_proceeds * (w / add_w)) if add_w else 0
            weight_delta = funded / nav if nav else 0
            tr = _build_trade(t, "BUY", weight_delta, nav, prices,
                              "new_position", min_trade)
            if tr is not None:
                trades.append(tr)
        p = _finish("A: Fund adds from removes", trades, partial)
        if p:
            paths.append(p)

    # ── PATH B: fund from overweight positions ──────────────────────────
    if adds:
        shortfall = add_cost - removal_proceeds
        trades = []
        partial = False
        if removals:
            for t, w in removals.items():
                tr = _build_trade(t, "SELL", -w, nav, prices, "removal", min_trade)
                if tr is not None:
                    trades.append(tr)
        if shortfall > 0:
            # Rank existing by (current-target)/target DESCENDING
            ranked = sorted(
                existing.items(),
                key=lambda kv: ((kv[1][0] - kv[1][1]) / kv[1][1]) if kv[1][1] else 1e9,
                reverse=True,
            )
            for t, (cur, tgt) in ranked:
                if shortfall <= 0:
                    break
                if tgt <= 0:
                    continue
                rel_over = (cur - tgt) / tgt
                if rel_over <= band:
                    continue  # within rebalancing band — skip
                trim = min(cur - tgt, shortfall / nav)  # weight to trim
                if trim <= 0:
                    continue
                trades.append(_build_trade(t, "SELL", -trim, nav, prices,
                                           "largest_overweight", min_trade))
                shortfall -= trim * nav
            if shortfall > 1e-9:
                partial = True
        # Now fund adds from accumulated proceeds (sum SELL notional magnitudes).
        funded_total = sum(abs(t["weight_delta"]) * nav
                           for t in trades if t["action"] == "SELL")
        add_w = sum(adds.values())
        for t, w in adds.items():
            funded = min(w * nav, funded_total * (w / add_w)) if add_w else 0
            weight_delta = funded / nav if nav else 0
            trades.append(_build_trade(t, "BUY", weight_delta, nav, prices,
                                       "new_position", min_trade))
        p = _finish("B: Trim overweights", trades, partial)
        if p:
            paths.append(p)

    # ── PATH C: fund from cash reserve (BIL) ────────────────────────────
    bil = current_weights.get("BIL", 0)
    if bil >= rb["cash_reserve_min_pct"]:
        trades = []
        bil_fund = min(bil, add_cost / nav)
        if bil_fund > 0:
            trades.append(_build_trade("BIL", "SELL", -bil_fund, nav, prices,
                                       "cash_reserve", min_trade))
        for t, w in adds.items():
            funded = min(w, bil_fund)  # capped by BIL available
            trades.append(_build_trade(t, "BUY", funded, nav, prices,
                                       "new_position", min_trade))
        partial = bil_fund < (add_cost / nav)
        p = _finish("C: Draw cash reserve", trades, partial)
        if p:
            paths.append(p)

    # ── PATH D: remove lowest-conviction position ───────────────────────
    if adds and existing:
        trades = []
        # Score each existing position: screener_agreement * ns2_confidence
        def _score(t):
            s = screener_scores.get(t, 0)
            n2 = ns2_regimes.get(t)
            conf = n2[1] if (n2 and isinstance(n2, (list, tuple)) and len(n2) > 1) else 0.5
            return s * conf

        lowest = min(existing.keys(), key=_score)
        cur_w, _ = existing[lowest]
        trades.append(_build_trade(lowest, "SELL", -cur_w, nav, prices,
                                   "lowest_conviction", min_trade))
        proceeds = cur_w * nav
        partial = proceeds < add_cost
        add_w = sum(adds.values())
        for t, w in adds.items():
            funded = min(w * nav, proceeds * (w / add_w)) if add_w else 0
            trades.append(_build_trade(t, "BUY", funded / nav, nav, prices,
                                       "new_position", min_trade))
        p = _finish("D: Remove lowest conviction", trades, partial)
        if p:
            paths.append(p)

    # ── STEP 4: rank & cap ──────────────────────────────────────────────
    # ranking_order: fewest_trades ASC → lowest_tax ASC → best_risk DESC
    priority = rb["ranking_order"]
    ranked = paths
    for criterion in reversed(priority):
        if criterion == "fewest_trades":
            ranked = sorted(ranked, key=lambda p: p["trade_count"])
        elif criterion == "lowest_tax":
            ranked = sorted(ranked, key=lambda p: p["tax_cost"])
        elif criterion == "best_risk":
            ranked = sorted(ranked, key=lambda p: -p["risk_impact"]["sharpe_delta"])
    return ranked[:max_paths]
