"""
tax_context.py — Tax-aware funding path ranking (Phase 3).

Implements the frontier spec: after-tax cost of a funding path (lot-level
gain calc, LTCG/STCG classification, TLH offset) and re-ranking by
after-tax cost. Pure functions — no API calls. Lot selection is
highest-cost-basis-first (minimise realized gain).

Tax rates come from tax_profile dict (not theta) — matching NS-5's pattern
of computing drags from bracket fields.
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

import config

log = logging.getLogger("ns6.tax_context")

LTCG_MAX = 0.20       # 2026 max long-term capital gains rate
NIIT = 0.038          # Net Investment Income Tax
STCG_HOLDING_DAYS = 365  # >365d → LTCG; <=365d → STCG


# ── Tax rate helpers ──────────────────────────────────────────────────────
def _marginal_ordinary(tax_profile) -> float:
    """STCG marginal rate: federal + state + NIIT."""
    fb = float(tax_profile.get("federal_bracket", 0.24))
    st = float(tax_profile.get("state_rate", 0.0))
    niit = NIIT if tax_profile.get("niit", False) else 0.0
    return fb + st + niit


def _marginal_ltcg(tax_profile) -> float:
    """LTCG marginal rate: 20% max + state + NIIT."""
    st = float(tax_profile.get("state_rate", 0.0))
    niit = NIIT if tax_profile.get("niit", False) else 0.0
    return LTCG_MAX + st + niit


def _holding_days(acquired: str):
    """Days since acquisition (approx) or None if unknown."""
    try:
        return (datetime.now() - datetime.fromisoformat(acquired)).days
    except (ValueError, TypeError):
        return None


# ── Lot selection ─────────────────────────────────────────────────────────
def _select_lots(ticker, shares_to_sell, tax_lot_data, sell_price):
    """Allocate a sell across lots, highest cost basis first.

    Returns (total_gain, ltcg_gain, stcg_gain, unclassified) in $.
    No lot data → worst case: entire proceeds taxable, treated as STCG.
    """
    position = tax_lot_data.get(ticker) if tax_lot_data else None
    lots = []
    if position and isinstance(position, dict):
        lots = position.get("lots") or []

    if not lots:
        # no cost basis — entire proceeds are gain, STCG (conservative)
        return sell_price * shares_to_sell, 0.0, sell_price * shares_to_sell, True

    # highest cost basis first → minimise realized gain
    lots_sorted = sorted(lots, key=lambda l: l.get("cost_per_share", 0.0), reverse=True)

    total_gain = ltcg = stcg = 0.0
    remaining = shares_to_sell
    for lot in lots_sorted:
        if remaining <= 0:
            break
        lot_shares = float(lot.get("shares", 0))
        cost = float(lot.get("cost_per_share", 0))
        if lot_shares <= 0:
            continue
        used = min(remaining, lot_shares)
        gain = (sell_price - cost) * used
        days = _holding_days(lot.get("date"))
        if days is None or days <= STCG_HOLDING_DAYS:
            stcg += gain
        else:
            ltcg += gain
        total_gain += gain
        remaining -= used

    # any unsold shares (lot data insufficient) → no basis, STCG
    if remaining > 1e-9:
        extra = sell_price * remaining
        total_gain += extra
        stcg += extra

    return total_gain, ltcg, stcg, False


# ── Tax cost ──────────────────────────────────────────────────────────────
def compute_funding_tax_cost(funding_path, tax_lot_data=None, tlh_available=0.0,
                             tax_profile=None, prices=None, theta=None) -> float:
    """After-tax cost (in $) of a funding path's SELL trades.

    funding_path : dict — from rebalance.generate_funding_paths()
    tax_lot_data : dict — {ticker: {lots: [{date, shares, cost_per_share}]}}
    tlh_available : float — unrealized ST losses harvestable ($)
    tax_profile  : dict — {federal_bracket, state_rate, niit}
    prices       : dict — {ticker: sell_price}; defaults to $0 → all-gain
    theta        : dict — config (unused for rates, kept for signature parity)

    Returns total tax cost in $ (positive = cost, 0 = fully offset).
    """
    tax_profile = tax_profile or {}
    prices = prices or {}
    tlh_available = max(float(tlh_available or 0.0), 0.0)

    stcg_rate = _marginal_ordinary(tax_profile)
    ltcg_rate = _marginal_ltcg(tax_profile)

    gross = 0.0
    stcg_gross = 0.0
    ltcg_gross = 0.0
    for trade in (funding_path or {}).get("trades", []):
        if trade.get("action") != "SELL":
            continue
        ticker = trade["ticker"]
        shares = float(trade.get("shares", 0))
        sell_price = float(prices.get(ticker, 0.0))
        _, ltcg_gain, stcg_gain, _ = _select_lots(ticker, shares, tax_lot_data, sell_price)
        ltcg_gross += ltcg_gain
        stcg_gross += stcg_gain
        gross += ltcg_gain + stcg_gain

    tax_stcg = stcg_gross * stcg_rate
    tax_ltcg = ltcg_gross * ltcg_rate

    # TLH offsets highest-rate gains FIRST (STCG at higher rate → first).
    remaining_tlh = tlh_available
    tax_ltcg = max(0.0, tax_ltcg - min(remaining_tlh, tax_ltcg))
    remaining_tlh = max(0.0, remaining_tlh - min(remaining_tlh, tax_ltcg))

    return max(0.0, tax_ltcg + tax_stcg)


# ── Ranking ───────────────────────────────────────────────────────────────
def rank_paths_by_after_tax_cost(paths, tax_lot_data=None, tlh_available=0.0,
                                 tax_profile=None, prices=None, theta=None) -> List[Dict]:
    """Re-rank funding paths by after-tax cost.

    Adds "after_tax_cost" key to each path in place, then sorts by
    (after_tax_cost ASC → trade_count ASC → sharpe_delta DESC).
    """
    theta = theta or config.load_theta()
    for p in paths:
        p["after_tax_cost"] = compute_funding_tax_cost(
            p, tax_lot_data=tax_lot_data, tlh_available=tlh_available,
            tax_profile=tax_profile, prices=prices, theta=theta)
    return sorted(
        paths,
        key=lambda p: (p["after_tax_cost"], p["trade_count"],
                       -p.get("risk_impact", {}).get("sharpe_delta", 0.0)),
    )


# ── Backtest proxy helpers (Phase 3 approximation, labeled as proxies) ────
def tax_drag_proxy(paths, nav=1_000_000.0) -> float:
    """TAX PROXY: flat drag = total SELL weight × 5% (fraction of NAV, one quarter).

    Approximates ~20% gain fraction × ~24% tax rate, in the absence of
    lot history. For backtest only — labeled proxy, not precise.
    Returns a fraction of NAV (0.05 × total sold weight).
    """
    sold_weight = 0.0
    for p in paths:
        for t in p.get("trades", []):
            if t["action"] == "SELL":
                sold_weight += abs(float(t.get("weight_delta", 0)))
    return sold_weight * 0.05


def covered_call_yield_proxy(multiplier, theta=None) -> float:
    """COVERED CALL PROXY: annualized yield when the gate allows overwrite.

    Returns daily yield boost (fraction of NAV) when multiplier ≥ gate;
    0.0 otherwise. Yield = 4%/yr × overwrite fraction.
    """
    theta = theta or config.load_theta()
    cc = theta["covered_calls"]
    if multiplier < cc["gate_multiplier"]:
        return 0.0
    if multiplier >= cc["full_threshold"]:
        overwrite = cc["overwrite_pct"]["full"]
    else:
        overwrite = cc["overwrite_pct"]["reduced"]
    annual = 0.04 * overwrite
    return annual / 252.0
