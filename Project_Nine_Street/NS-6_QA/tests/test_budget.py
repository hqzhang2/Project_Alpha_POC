"""Test budget.py — drawdown tracking & budget computation. Synthetic + offline."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import budget
from config import load_theta


def test_compute_drawdown_no_drawdown():
    # steadily rising → drawdown 0
    prices = [100, 101, 102, 103, 104]
    assert budget.compute_drawdown(prices) == pytest.approx(0.0, abs=1e-9)


def test_compute_drawdown_drawdown():
    # 100 → 120 (peak) → 110 → 105 → 108
    prices = [100, 110, 120, 110, 105, 108]
    # peak=120, last=108 → (108/120)-1 = -0.10
    assert budget.compute_drawdown(prices) == pytest.approx(-0.10, abs=1e-9)


def test_compute_drawdown_from_all_time_high_not_last_peak():
    # peak is mid-series; recovery above peak then new peak
    prices = [100, 90, 110, 120, 115]
    assert budget.compute_drawdown(prices) == pytest.approx((115 / 120) - 1, abs=1e-9)


def test_compute_drawdown_empty():
    assert budget.compute_drawdown([]) == 0.0


def test_compute_drawdown_single():
    assert budget.compute_drawdown([100]) == 0.0


def test_compute_drawdown_none_values_skipped():
    prices = [100, None, 110, None, 105]
    assert budget.compute_drawdown(prices) == pytest.approx((105 / 110) - 1, abs=1e-9)


def test_spy_drawdown_same_math():
    assert budget.compute_spy_drawdown([100, 90]) == pytest.approx(-0.10, abs=1e-9)


def test_budget_half_of_spy_floor_applies():
    # SPY down 8% → half = 4%, but floor 5% guarantees ≥5% → budget 5%
    assert budget.compute_budget(-0.08) == pytest.approx(-0.05, abs=1e-9)


def test_budget_deep_spy_half_exceeds_floor():
    # SPY down 20% → half = 10% > floor 5% → budget 10%
    assert budget.compute_budget(-0.20) == pytest.approx(-0.10, abs=1e-9)


def test_budget_floor_when_spy_shallow():
    # SPY down 2% → naive -1%, floor -5% applies
    assert budget.compute_budget(-0.02) == pytest.approx(-0.05, abs=1e-9)


def test_budget_none_spy_uses_floor():
    assert budget.compute_budget(None) == pytest.approx(-0.05, abs=1e-9)


def test_budget_positive_spy_no_drawdown():
    # SPY up → budget = floor (still have 5% rope)
    assert budget.compute_budget(0.05) == pytest.approx(-0.05, abs=1e-9)


def test_budget_remaining_mid():
    # dd=-2%, budget=-5% → 1 - (2/5) = 0.6
    assert budget.budget_remaining(-0.02, -0.05) == pytest.approx(0.6, abs=1e-9)


def test_budget_remaining_full_budget_consumed():
    assert budget.budget_remaining(-0.05, -0.05) == pytest.approx(0.0, abs=1e-9)


def test_budget_remaining_over_budget_clamps_zero():
    assert budget.budget_remaining(-0.07, -0.05) == 0.0


def test_budget_remaining_no_drawdown_clamps_one():
    assert budget.budget_remaining(0.0, -0.05) == 1.0


def test_budget_remaining_positive_dd_clamps_one():
    # positive dd (above peak) → remaining > 1 → clamp to 1
    assert budget.budget_remaining(0.02, -0.05) == 1.0


def test_budget_remaining_zero_budget():
    assert budget.budget_remaining(-0.02, 0.0) == 1.0


def test_budget_remaining_none_inputs():
    assert budget.budget_remaining(None, -0.05) == 1.0
    assert budget.budget_remaining(-0.02, None) == 1.0


def test_status_snapshot():
    theta = load_theta()
    snap = budget.status_snapshot([100, 120, 110], [100, 90], theta)
    assert snap["current_drawdown_pct"] == pytest.approx(-0.0833, abs=0.001)
    assert snap["spy_drawdown_pct"] == pytest.approx(-0.10, abs=0.001)
    assert snap["budget_pct"] == pytest.approx(-0.05, abs=0.001)
    assert snap["budget_remaining_pct"] == pytest.approx(0.0, abs=0.001)
