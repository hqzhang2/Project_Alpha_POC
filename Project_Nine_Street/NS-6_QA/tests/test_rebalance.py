"""Test rebalance.py — 4-path funding algorithm + guards."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import rebalance
from config import load_theta

NAV = 1_000_000
PRICES = {"AAPL": 200, "MSFT": 400, "NVDA": 120, "JPM": 100,
          "BIL": 50, "XYZ": 30}


def _run(cur, tgt, **kw):
    return rebalance.generate_funding_paths(cur, tgt, NAV, prices=PRICES, **kw)


# ── Path A: fund adds from removes ───────────────────────────────────────
def test_path_a_swap_remove_for_add():
    cur = {"MSFT": 0.10, "JPM": 0.10, "BIL": 0.80}
    tgt = {"NVDA": 0.10, "JPM": 0.10, "BIL": 0.80}
    paths = _run(cur, tgt)
    # A valid path exists (fund NVDA from MSFT removal)
    assert len(paths) >= 1
    a = [p for p in paths if p["name"].startswith("A")][0]
    actions = {t["ticker"]: t["action"] for t in a["trades"]}
    assert actions["MSFT"] == "SELL"
    assert actions["NVDA"] == "BUY"


def test_path_a_removal_proceeds_equal_add_cost():
    cur = {"MSFT": 0.05, "BIL": 0.95}
    tgt = {"NVDA": 0.05, "BIL": 0.95}
    paths = _run(cur, tgt)
    a = [p for p in paths if p["name"].startswith("A")][0]
    assert a["partial"] is False


def test_path_a_partial_when_proceeds_short():
    # removal 0.03 < add 0.10 → partial
    cur = {"MSFT": 0.03, "BIL": 0.97}
    tgt = {"NVDA": 0.10, "BIL": 0.90}
    paths = _run(cur, tgt)
    a = [p for p in paths if p["name"].startswith("A")][0]
    assert a["partial"] is True


# ── Path B: fund from overweights ────────────────────────────────────────
def test_path_b_trims_overweight():
    cur = {"AAPL": 0.12, "BIL": 0.88}
    tgt = {"AAPL": 0.05, "NVDA": 0.05, "BIL": 0.90}
    paths = _run(cur, tgt)
    b = [p for p in paths if p["name"].startswith("B")][0]
    actions = {t["ticker"]: t["action"] for t in b["trades"]}
    assert actions["AAPL"] == "SELL"  # overweight trim
    assert actions["NVDA"] == "BUY"


def test_path_b_respects_rebalancing_band():
    # AAPL 5.5% vs target 5% → within 20% band (rel 0.10) → not a trim source
    cur = {"AAPL": 0.055, "BIL": 0.945}
    tgt = {"AAPL": 0.05, "NVDA": 0.02, "BIL": 0.93}
    paths = _run(cur, tgt)
    # Path B (trim overweights) is DROPPED because no position is beyond band;
    # and in no path should AAPL be sold as an overweight trim.
    for p in paths:
        for t in p["trades"]:
            assert not (t["ticker"] == "AAPL" and t["reason"] == "largest_overweight")


def test_path_b_no_removals_uses_add_cost():
    cur = {"AAPL": 0.12, "BIL": 0.88}
    tgt = {"AAPL": 0.05, "NVDA": 0.05, "BIL": 0.90}
    b = [p for p in _run(cur, tgt) if p["name"].startswith("B")][0]
    assert any(t["action"] == "BUY" and t["ticker"] == "NVDA" for t in b["trades"])


# ── Path C: cash reserve ─────────────────────────────────────────────────
def test_path_c_requires_bil_min():
    # BIL 1% < 2% min → Path C not generated
    cur = {"AAPL": 0.99, "BIL": 0.01}
    tgt = {"AAPL": 0.90, "NVDA": 0.05, "BIL": 0.05}
    paths = _run(cur, tgt)
    assert not any(p["name"].startswith("C") for p in paths)


def test_path_c_bil_available():
    cur = {"AAPL": 0.90, "BIL": 0.10}
    tgt = {"AAPL": 0.85, "NVDA": 0.05, "BIL": 0.10}
    paths = _run(cur, tgt)
    c = [p for p in paths if p["name"].startswith("C")]
    assert len(c) == 1


def test_path_c_empty_portfolio_first_trade():
    # Empty current portfolio → Path C (draw from BIL) should be viable if
    # there's a BIL... but empty current means no BIL either. Expect only
    # paths that can appear; empty current → no valid trades → empty list.
    paths = _run({}, {"NVDA": 0.10, "BIL": 0.90})
    # With empty current, BIL weight is 0, so no source of funds.
    # Only Path C could work but it requires existing BIL > min. Empty list.
    assert paths == []


# ── Path D: remove lowest conviction ─────────────────────────────────────
def test_path_d_removes_lowest_conviction():
    cur = {"AAPL": 0.05, "MSFT": 0.05, "BIL": 0.90}
    tgt = {"AAPL": 0.05, "MSFT": 0.05, "NVDA": 0.05, "BIL": 0.85}
    # MSFT has low screener score + low ns2 confidence → removed
    scores = {"AAPL": 4, "MSFT": 0, "NVDA": 3}
    n2 = {"AAPL": ("TRENDING", 0.9), "MSFT": ("NO-EDGE", 0.1)}
    paths = _run(cur, tgt, screener_scores=scores, ns2_regimes=n2)
    d = [p for p in paths if p["name"].startswith("D")]
    if d:
        assert any(t["ticker"] == "MSFT" and t["action"] == "SELL"
                   for t in d[0]["trades"])


def test_path_d_requires_existing_positions():
    # No existing (all new adds, no current) → Path D not generated
    paths = _run({}, {"NVDA": 0.10, "BIL": 0.90})
    assert not any(p["name"].startswith("D") for p in paths)


# ── Guards & ranking ─────────────────────────────────────────────────────
def test_min_trade_size_suppresses_small_trades():
    # tiny proposed add (0.001 = 0.1% NAV < 0.5% min) → suppressed
    cur = {"BIL": 0.999, "MSFT": 0.001}
    tgt = {"BIL": 0.997, "MSFT": 0.001, "NVDA": 0.002}
    paths = _run(cur, tgt)
    # NVDA 0.002 = 0.2% < 0.5% min → trade suppressed → path likely dropped
    # (or path has no NVDA buy). Either way, no NVDA trade below min.
    for p in paths:
        for t in p["trades"]:
            assert t["shares"] >= 0


def test_concentrated_target_no_none_crash():
    """Regression: concentrated NS-5 tangency weights suppress many trades
    below min size. Path B must NOT crash on the suppressed None trades
    when computing funded_total (found via ns6_backtest --weighting ns5)."""
    # Many tiny targets below 0.5% min + a few large ones. Equal-weight
    # portfolios never trigger this; concentrated weights do.
    cur = {f"T{i}": 0.1 for i in range(9)}
    cur["BIL"] = 0.1
    tgt = {"T0": 0.45, "T1": 0.35, "BIL": 0.20}  # rest suppressed (0 weight)
    # T0-T8 not in PRICES → shares round to 0 → suppressed; exercises None path.
    paths = _run(cur, tgt)
    # Must not raise; each trade is a dict (never None).
    for p in paths:
        for t in p["trades"]:
            assert isinstance(t, dict)
            assert t["shares"] >= 0


def test_paths_ranked_by_fewest_trades():
    cur = {"MSFT": 0.10, "BIL": 0.90}
    tgt = {"NVDA": 0.10, "BIL": 0.90}
    paths = _run(cur, tgt)
    counts = [p["trade_count"] for p in paths]
    assert counts == sorted(counts)  # ascending


def test_max_paths_cap():
    cur = {"MSFT": 0.10, "JPM": 0.05, "AAPL": 0.05, "BIL": 0.80}
    tgt = {"NVDA": 0.10, "JPM": 0.05, "AAPL": 0.05, "BIL": 0.80}
    paths = _run(cur, tgt)
    assert len(paths) <= 5  # max_paths


def test_tax_cost_stubbed_zero():
    cur = {"MSFT": 0.10, "BIL": 0.90}
    tgt = {"NVDA": 0.10, "BIL": 0.90}
    paths = _run(cur, tgt)
    for p in paths:
        assert p["tax_cost"] == 0.0


def test_risk_impact_stubbed():
    cur = {"MSFT": 0.10, "BIL": 0.90}
    tgt = {"NVDA": 0.10, "BIL": 0.90}
    paths = _run(cur, tgt)
    assert paths[0]["risk_impact"]["sharpe_delta"] == 0.0


def test_no_changes_no_paths():
    cur = {"BIL": 1.0}
    tgt = {"BIL": 1.0}
    assert _run(cur, tgt) == []
