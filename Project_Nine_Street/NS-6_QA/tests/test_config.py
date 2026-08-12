"""Test config.py — THETA structure, immutability, deep-copy merge."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from config import THETA_DEFAULTS, load_theta

REQUIRED_TOP = [
    "budget", "multiplier", "circuit_breakers", "position_stops",
    "hysteresis", "rebalancing", "covered_calls", "protective_puts",
    "iron_condor", "drift_alert", "scenario",
]


def test_all_top_level_keys_present():
    for k in REQUIRED_TOP:
        assert k in THETA_DEFAULTS, f"missing {k}"


def test_theta_immutability():
    """load_theta() returns a fresh deep copy — mutating it must not touch defaults."""
    t = load_theta()
    t["budget"]["hard_floor"] = 0.99
    assert THETA_DEFAULTS["budget"]["hard_floor"] == 0.25


def test_deep_copy_nested():
    t = load_theta()
    t["multiplier"]["regime_budget_factors"]["R1"] = 0.5
    assert THETA_DEFAULTS["multiplier"]["regime_budget_factors"]["R1"] == 1.0


def test_budget_sane():
    b = THETA_DEFAULTS["budget"]
    assert 0 < b["absolute_floor_bps"] < 2000
    assert 0 < b["hard_floor"] < 1
    assert 0 < b["spy_dd_ratio"] <= 1


def test_regime_factors_monotonic():
    r = THETA_DEFAULTS["multiplier"]["regime_budget_factors"]
    assert r["R1"] >= r["R2"] >= r["R3"] >= r["R4"] >= 0


def test_position_stops_negative():
    for cls, thresh in THETA_DEFAULTS["position_stops"].items():
        assert thresh <= 0, f"{cls} stop should be negative"


def test_drift_bands_ordered():
    da = THETA_DEFAULTS["drift_alert"]
    assert da["band_rel_urgent"] > da["band_rel_warning"] > 0


def test_covered_call_overwrite_ordering():
    cc = THETA_DEFAULTS["covered_calls"]
    assert cc["overwrite_pct"]["full"] > cc["overwrite_pct"]["reduced"] > 0


def test_load_theta_override_merge():
    t = load_theta({"budget": {"hard_floor": 0.30}})
    assert t["budget"]["hard_floor"] == 0.30
    # non-overridden sibling preserved
    assert t["budget"]["absolute_floor_bps"] == THETA_DEFAULTS["budget"]["absolute_floor_bps"]


def test_load_theta_override_new_key():
    t = load_theta({"custom": {"x": 1}})
    assert t["custom"] == {"x": 1}


def test_letter_bounds_descending():
    bounds = [(4.5, "A"), (3.5, "B"), (2.5, "C"), (1.5, "D"), (0.0, "F")]
    assert bounds == sorted(bounds, reverse=True)  # descending


def test_severity_bounds_descending():
    sev = [(5.0, "green"), (3.5, "yellow"), (2.0, "orange"), (0.0, "red")]
    assert sev == sorted(sev, reverse=True)  # descending (NS-5 pitfall guard)
