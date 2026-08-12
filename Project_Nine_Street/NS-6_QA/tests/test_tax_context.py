"""Test tax_context.py — after-tax funding path ranking + proxies."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import tax_context
from config import load_theta

PROFILE = {"federal_bracket": 0.24, "state_rate": 0.05, "niit": True}
# marginal: ordinary = 0.24+0.05+0.038 = 0.328; ltcg = 0.20+0.05+0.038 = 0.288


def _path(trades):
    return {"name": "test", "trades": trades, "trade_count": len(trades),
            "risk_impact": {"sharpe_delta": 0.0}}


def _sell(ticker, shares, price):
    return {"ticker": ticker, "action": "SELL", "shares": shares,
            "weight_delta": -0.05, "reason": "test"}


# ── no lot data → all-gain STCG ──────────────────────────────────────────
def test_no_lot_data_all_gain_stcg():
    p = _path([_sell("AAPL", 10, 100)])
    # sell notional 1000, all gain, STCG rate 0.328
    cost = tax_context.compute_funding_tax_cost(p, {}, 0, PROFILE, {"AAPL": 100})
    assert cost == pytest.approx(1000 * 0.328, abs=0.5)


# ── lot selection highest cost basis first ───────────────────────────────
def test_highest_cost_basis_first():
    lots = {"AAPL": {"lots": [
        {"date": "2020-01-01", "shares": 5, "cost_per_share": 50},   # LTCG, gain 50/sh
        {"date": "2021-01-01", "shares": 5, "cost_per_share": 20},   # LTCG, gain 80/sh
    ]}}
    p = _path([_sell("AAPL", 10, 100)])
    # both LTCG (held >365d): gain = 5*50 + 5*80 = 650, rate 0.288
    cost = tax_context.compute_funding_tax_cost(p, lots, 0, PROFILE, {"AAPL": 100})
    assert cost == pytest.approx(650 * 0.288, abs=0.5)


# ── TLH offsets ──────────────────────────────────────────────────────────
def test_tlh_offsets_cost():
    lots = {"AAPL": {"lots": [
        {"date": "2020-01-01", "shares": 10, "cost_per_share": 50},
    ]}}
    p = _path([_sell("AAPL", 10, 100)])
    no_tlh = tax_context.compute_funding_tax_cost(p, lots, 0, PROFILE, {"AAPL": 100})
    # gain = 10*50 = 500, LTCG rate 0.288 → 144
    assert no_tlh == pytest.approx(500 * 0.288, abs=0.5)
    with_tlh = tax_context.compute_funding_tax_cost(p, lots, 200, PROFILE, {"AAPL": 100})
    # TLH 200 offsets first dollar of LTCG 144 → 0
    assert with_tlh == 0.0


def test_tlh_partial_offset():
    lots = {"AAPL": {"lots": [
        {"date": "2020-01-01", "shares": 10, "cost_per_share": 50},
    ]}}
    p = _path([_sell("AAPL", 10, 100)])
    # tax 144, tlh 50 → 94
    cost = tax_context.compute_funding_tax_cost(p, lots, 50, PROFILE, {"AAPL": 100})
    assert cost == pytest.approx(144 - 50, abs=0.5)


# ── STCG vs LTCG classification ─────────────────────────────────────────
def test_recent_lot_stcg_higher_rate():
    # held 100 days → STCG, rate 0.328 > ltcg 0.288
    lots = {"AAPL": {"lots": [
        {"date": "2026-05-01", "shares": 10, "cost_per_share": 50},  # ~100d → STCG
    ]}}
    p = _path([_sell("AAPL", 10, 100)])
    cost = tax_context.compute_funding_tax_cost(p, lots, 0, PROFILE, {"AAPL": 100})
    assert cost == pytest.approx(500 * 0.328, abs=0.5)


def test_unknown_date_treated_stcg():
    lots = {"AAPL": {"lots": [
        {"date": None, "shares": 10, "cost_per_share": 50},
    ]}}
    p = _path([_sell("AAPL", 10, 100)])
    cost = tax_context.compute_funding_tax_cost(p, lots, 0, PROFILE, {"AAPL": 100})
    assert cost == pytest.approx(500 * 0.328, abs=0.5)  # STCG conservative


# ── ranking ──────────────────────────────────────────────────────────────
def test_rank_by_after_tax_cost():
    # cheap path: X has high cost basis (99) → tiny gain → low tax
    # expensive path: Y has no lot data → all-gain STCG → high tax
    lots = {"X": {"lots": [{"date": "2020-01-01", "shares": 10, "cost_per_share": 99}]}}
    cheap = _path([{"ticker": "X", "action": "SELL", "shares": 10,
                    "weight_delta": -0.05, "reason": "test"}])
    expensive = _path([{"ticker": "Y", "action": "SELL", "shares": 10,
                        "weight_delta": -0.05, "reason": "test"}])
    ranked = tax_context.rank_paths_by_after_tax_cost(
        [expensive, cheap], lots, 0, PROFILE, {"X": 100, "Y": 100})
    assert ranked[0]["name"] == cheap["name"]  # cheap (low tax) first
    assert ranked[0]["after_tax_cost"] <= ranked[1]["after_tax_cost"]
    assert "after_tax_cost" in ranked[0]


# ── proxies ──────────────────────────────────────────────────────────────
def test_tax_drag_proxy():
    p = _path([{"ticker": "A", "action": "SELL", "shares": 10,
                "weight_delta": -0.10, "reason": "test"}])
    # sold weight 0.10 × 5% = 0.005 (fraction of NAV, not dollars)
    assert tax_context.tax_drag_proxy([p], 1_000_000) == pytest.approx(0.005)


def test_call_yield_proxy_gated():
    theta = load_theta()
    # multiplier 0.50 < gate 0.60 → 0
    assert tax_context.covered_call_yield_proxy(0.50, theta) == 0.0
    # multiplier 0.70 ≥ gate, < 0.80 full → reduced 25%: 0.04*0.25/252
    assert tax_context.covered_call_yield_proxy(0.70, theta) == pytest.approx(0.04 * 0.25 / 252)
    # multiplier 0.90 ≥ 0.80 → full 50%: 0.04*0.50/252
    assert tax_context.covered_call_yield_proxy(0.90, theta) == pytest.approx(0.04 * 0.50 / 252)
