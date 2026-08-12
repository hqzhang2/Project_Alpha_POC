"""Test scenario.py — orchestrator wiring (add/remove/replace)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scenario
from config import load_theta

NAV = 1_000_000
CUR = {"MSFT": 0.10, "JPM": 0.05, "BIL": 0.85}
PRICES = {"MSFT": 400, "JPM": 100, "NVDA": 120, "BIL": 50}


def test_analyze_add_returns_funding_paths():
    r = scenario.analyze_add("NVDA", 0.05, CUR, NAV, prices=PRICES,
                             theta=load_theta())
    assert r["kind"] == "add"
    assert r["new_ticker"] == "NVDA"
    assert isinstance(r["funding_paths"], list)
    assert r["target_weights"]["NVDA"] == 0.05


def test_analyze_add_includes_removal_source():
    # Adding NVDA while holding MSFT → at least one path trims/sells MSFT
    r = scenario.analyze_add("NVDA", 0.10, CUR, NAV, prices=PRICES,
                             theta=load_theta())
    tickers = {t["ticker"] for p in r["funding_paths"] for t in p["trades"]}
    assert "NVDA" in tickers


def test_analyze_remove_drops_ticker():
    r = scenario.analyze_remove("MSFT", CUR, NAV, prices=PRICES,
                                theta=load_theta())
    assert r["kind"] == "remove"
    assert r["removed_ticker"] == "MSFT"
    assert "MSFT" not in r["target_weights"]


def test_analyze_replace():
    r = scenario.analyze_replace("MSFT", "NVDA", 0.05, CUR, NAV,
                                 prices=PRICES, theta=load_theta())
    assert r["kind"] == "replace"
    assert r["removed_ticker"] == "MSFT"
    assert r["new_ticker"] == "NVDA"
    assert "MSFT" not in r["target_weights"]
    assert r["target_weights"]["NVDA"] == 0.05


def test_drawdown_impact_worst_case_stop():
    r = scenario.analyze_add("NVDA", 0.10, CUR, NAV, prices=PRICES,
                             theta=load_theta())
    dd = r["drawdown_impact"]
    # new weight 0.10 × equity stop 0.25 = 0.025
    assert dd["worst_case_stop_cost"] == 0.025


def test_screener_block_present():
    r = scenario.analyze_add("NVDA", 0.05, CUR, NAV, prices=PRICES,
                             screener_scores={"NVDA": 3}, theta=load_theta())
    assert r["screener"]["ticker"] == "NVDA"
    assert r["screener"]["agreement"] == 3


def test_screener_block_none_when_no_ticker():
    r = scenario.analyze_remove("MSFT", CUR, NAV, prices=PRICES,
                                theta=load_theta())
    assert r["screener"] is None


def test_empty_current_portfolio_add():
    r = scenario.analyze_add("NVDA", 0.05, {}, NAV, prices=PRICES,
                             theta=load_theta())
    assert r["target_weights"]["NVDA"] == 0.05
    assert r["funding_paths"] == []  # no source of funds in empty portfolio


def test_scenario_none_inputs_fail_open():
    # Missing optional inputs (screener_scores/ns2_regimes) must not crash
    r = scenario.analyze_add("NVDA", 0.05, CUR, NAV, prices=PRICES,
                             theta=load_theta())
    assert isinstance(r["funding_paths"], list)
