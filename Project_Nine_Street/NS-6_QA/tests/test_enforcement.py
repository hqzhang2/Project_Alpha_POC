"""Test enforcement.py — exposure multiplier (P1+P2), breakers, stops, hysteresis."""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import enforcement
from config import load_theta


# ── Phase 1: linear multiplier ───────────────────────────────────────────
def test_p1_full_budget():
    assert enforcement.compute_exposure_multiplier(1.0) == pytest.approx(1.0)


def test_p1_half_budget():
    assert enforcement.compute_exposure_multiplier(0.5) == pytest.approx(0.5)


def test_p1_at_floor():
    assert enforcement.compute_exposure_multiplier(0.25) == pytest.approx(0.25)


def test_p1_zero_budget_floor_override():
    assert enforcement.compute_exposure_multiplier(0.0) == pytest.approx(0.25)


def test_p1_negative_budget_floor_override():
    assert enforcement.compute_exposure_multiplier(-0.2) == pytest.approx(0.25)


def test_p1_above_one_clamped():
    assert enforcement.compute_exposure_multiplier(1.5) == pytest.approx(1.0)


def test_p1_none_fail_open_full():
    assert enforcement.compute_exposure_multiplier(None) == pytest.approx(1.0)


# ── Phase 2: multi-signal multiplier ─────────────────────────────────────
def test_p2_no_signals_clean_r1():
    # R1, no tiers → base × 1.0 = 0.6
    m = enforcement.compute_exposure_multiplier_v2(0.6, "R1", 1.0, -0.3, 15.0, -0.5)
    assert m == pytest.approx(0.6)


def test_p2_vol_tier_reduces():
    # vol_ratio=2.0 > 1.5 → 1 tier
    m = enforcement.compute_exposure_multiplier_v2(0.6, "R1", 2.0, -0.3, 15.0, -0.5)
    assert m == pytest.approx(0.6 - 0.15)


def test_p2_corr_tier_reduces():
    m = enforcement.compute_exposure_multiplier_v2(0.6, "R1", 1.0, 0.5, 15.0, -0.5)
    assert m == pytest.approx(0.6 - 0.15)


def test_p2_vix_tier_requires_rising():
    # VIX > 28 AND rising → tier
    m = enforcement.compute_exposure_multiplier_v2(0.6, "R1", 1.0, -0.3, 31.0, 1.0)
    assert m == pytest.approx(0.6 - 0.15)


def test_p2_vix_high_but_falling_no_tier():
    # VIX high but falling → no tier
    m = enforcement.compute_exposure_multiplier_v2(0.6, "R1", 1.0, -0.3, 31.0, -1.0)
    assert m == pytest.approx(0.6)


def test_p2_all_three_tiers_floor_override():
    # 0.6 base - 0.45 = 0.15 → floor 0.25 overrides
    m = enforcement.compute_exposure_multiplier_v2(0.6, "R1", 2.0, 0.5, 31.0, 1.0)
    assert m == pytest.approx(0.25)


def test_p2_three_tiers_floor_override():
    # 0.4 base - 0.45 = -0.05 → floor 0.25
    m = enforcement.compute_exposure_multiplier_v2(0.4, "R1", 2.0, 0.5, 31.0, 1.0)
    assert m == pytest.approx(0.25)


def test_p2_regime_factor_scales_budget():
    # R3 = 0.5 factor, 0.6 budget, no tiers → 0.3
    m = enforcement.compute_exposure_multiplier_v2(0.6, "R3", 1.0, -0.3, 15.0, -0.5)
    assert m == pytest.approx(0.3)


def test_p2_regime_r4_floor():
    # R4 factor 0.25: 0.6*0.25 = 0.15 → floor 0.25
    m = enforcement.compute_exposure_multiplier_v2(0.6, "R4", 1.0, -0.3, 15.0, -0.5)
    assert m == pytest.approx(0.25)


def test_p2_unknown_regime_defaults_r1():
    m = enforcement.compute_exposure_multiplier_v2(0.6, None, 1.0, -0.3, 15.0, -0.5)
    assert m == pytest.approx(0.6)


def test_p2_none_signal_no_tier():
    # None vol_ratio → no tier from it
    m = enforcement.compute_exposure_multiplier_v2(0.6, "R1", None, -0.3, 15.0, -0.5)
    assert m == pytest.approx(0.6)


def test_p2_staleness_phantom_tier():
    ages = {"vol_ratio": 5, "corr_sign": 1, "vix": 0}  # one stale
    m = enforcement.compute_exposure_multiplier_v2(
        0.6, "R1", 1.0, -0.3, 15.0, -0.5, signals_age_days=ages)
    assert m == pytest.approx(0.6 - 0.15)


def test_p2_no_staleness_no_penalty():
    ages = {"vol_ratio": 1, "corr_sign": 1, "vix": 1}  # all fresh
    m = enforcement.compute_exposure_multiplier_v2(
        0.6, "R1", 1.0, -0.3, 15.0, -0.5, signals_age_days=ages)
    assert m == pytest.approx(0.6)


def test_p2_max_tiers_cap():
    # All 3 active but max_tiers=3 → cap, no overflow. Verify not below floor.
    m = enforcement.compute_exposure_multiplier_v2(0.6, "R1", 2.0, 0.5, 31.0, 1.0)
    assert m >= 0.25


# ── Phase 4: circuit breakers ────────────────────────────────────────────
def test_hard_floor_not_triggered():
    # dd=-2%, budget=-5%, floor_trigger=0.9 → threshold=4.5%, -2% not >= -4.5%
    b = enforcement.check_circuit_breakers(-0.02, -0.05)
    hf = [x for x in b if x["type"] == "hard_floor"][0]
    assert hf["triggered"] is False


def test_hard_floor_triggered():
    # dd=-4.8% of budget -5% → 4.8 >= 4.5 → triggered
    b = enforcement.check_circuit_breakers(-0.048, -0.05)
    hf = [x for x in b if x["type"] == "hard_floor"][0]
    assert hf["triggered"] is True


def test_hard_floor_just_past_threshold():
    # dd=-4.9% of budget -5% → 98% consumed > 90% → triggered
    b = enforcement.check_circuit_breakers(-0.049, -0.05)
    hf = [x for x in b if x["type"] == "hard_floor"][0]
    assert hf["triggered"] is True


def test_systemic_event_not_triggered_low_corr():
    pos = {"A": -0.20, "B": -0.20, "C": -0.05}  # 2/3 down >15%
    b = enforcement.check_circuit_breakers(-0.02, -0.05, pos, 0.2)
    se = [x for x in b if x["type"] == "systemic_event"][0]
    assert se["triggered"] is False  # corr too low


def test_systemic_event_triggered():
    pos = {"A": -0.20, "B": -0.20, "C": -0.20}  # 3/3 down >15%
    b = enforcement.check_circuit_breakers(-0.02, -0.05, pos, 0.8)
    se = [x for x in b if x["type"] == "systemic_event"][0]
    assert se["triggered"] is True


def test_systemic_event_threshold_boundary():
    pos = {"A": -0.20, "B": -0.20, "C": -0.05, "D": -0.05, "E": -0.05}  # 2/5 = 40%
    b = enforcement.check_circuit_breakers(-0.02, -0.05, pos, 0.8)
    se = [x for x in b if x["type"] == "systemic_event"][0]
    assert se["triggered"] is False  # 40% < 60%


def test_systemic_event_missing_inputs_fail_open():
    b = enforcement.check_circuit_breakers(-0.02, -0.05)
    se = [x for x in b if x["type"] == "systemic_event"][0]
    assert se["triggered"] is False


# ── Phase 4: position stops ──────────────────────────────────────────────
def test_position_stop_equity_triggered():
    stops = enforcement.check_position_stops(
        {"AAPL": -0.30}, {"AAPL": "equity"})
    assert stops[0]["triggered"] is True


def test_position_stop_equity_not_triggered():
    stops = enforcement.check_position_stops(
        {"AAPL": -0.20}, {"AAPL": "equity"})
    assert stops[0]["triggered"] is False


def test_position_stop_bond_etf():
    # bond threshold -0.15; -0.16 triggers, -0.10 doesn't
    stops = enforcement.check_position_stops(
        {"TLT": -0.16, "IEF": -0.10}, {"TLT": "bond_etf", "IEF": "bond_etf"})
    by = {s["ticker"]: s for s in stops}
    assert by["TLT"]["triggered"] is True
    assert by["IEF"]["triggered"] is False


def test_position_stop_unknown_default():
    stops = enforcement.check_position_stops({"XYZ": -0.30}, {})
    assert stops[0]["triggered"] is True  # unknown default -0.20


def test_position_stop_empty():
    assert enforcement.check_position_stops({}) == []


def test_position_stop_action_string():
    stops = enforcement.check_position_stops({"AAPL": -0.30}, {"AAPL": "equity"})
    assert "Exit AAPL" in stops[0]["action"]
    assert "20 trading days" in stops[0]["action"]


# ── Phase 4: re-entry hysteresis ─────────────────────────────────────────
def test_hysteresis_no_lockout():
    assert enforcement.check_reentry_hysteresis(None, {}) is False


def test_hysteresis_breaker_blocks():
    theta = load_theta()
    now = datetime(2026, 8, 11, 12, 0, 0)
    breaker = now - timedelta(days=2)  # within 5*5/7≈3.6 days
    assert enforcement.check_reentry_hysteresis(breaker, {}, current_time=now,
                                                theta=theta) is True


def test_hysteresis_breaker_expired():
    theta = load_theta()
    now = datetime(2026, 8, 11, 12, 0, 0)
    breaker = now - timedelta(days=10)  # outside window
    assert enforcement.check_reentry_hysteresis(breaker, {}, current_time=now,
                                                theta=theta) is False


def test_hysteresis_position_stop_blocks():
    theta = load_theta()
    now = datetime(2026, 8, 11, 12, 0, 0)
    stops = {"AAPL": now - timedelta(days=5)}  # within 20*5/7≈14.3 days
    assert enforcement.check_reentry_hysteresis(None, stops, current_time=now,
                                                theta=theta) is True


def test_hysteresis_position_stop_expired():
    theta = load_theta()
    now = datetime(2026, 8, 11, 12, 0, 0)
    stops = {"AAPL": now - timedelta(days=30)}
    assert enforcement.check_reentry_hysteresis(None, stops, current_time=now,
                                                theta=theta) is False
