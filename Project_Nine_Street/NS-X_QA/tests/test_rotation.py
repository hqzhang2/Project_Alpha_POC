"""tests/test_rotation.py — NS-X rotation signal + weighting logic."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import rotation
import vol


def _mk(mean, vol_, n=300, seed=1):
    import random
    random.seed(seed)
    return [mean + random.gauss(0, vol_) for _ in range(n)]


# ── vol / momentum ───────────────────────────────────────────────────────
def test_exante_vol_positive():
    r = [0.01] * 200
    assert vol.exante_vol(r) is not None and vol.exante_vol(r) >= 0


def test_normalized_returns_zero_vol_flat():
    # zero-vol constant series -> flat (momentum 0), not None/explode
    norm = rotation.normalized_returns([0.002] * 200)
    assert all(v == 0.0 for v in norm)


def test_momentum_sums_window_not_product():
    up = _mk(0.002, 0.001)
    m = rotation.strategy_momentum(up)
    assert m is not None
    # summed vol-normalized return over ~105-day window, not a compounded product
    assert -1000 < m < 1000   # no numerical explosion


def test_momentum_up_positive_down_negative():
    up = _mk(0.002, 0.001, seed=2)
    down = _mk(-0.002, 0.001, seed=2)
    assert rotation.strategy_momentum(up) > 0
    assert rotation.strategy_momentum(down) < 0


def test_momentum_none_on_short_history():
    assert rotation.strategy_momentum([0.01, 0.01]) is None


# ── weighting ────────────────────────────────────────────────────────────
ROLES = {"ns7": "return", "at_val": "defensive", "ns8": "diversifier", "cash": "riskoff"}


def test_weights_sum_to_one_long_only():
    w = rotation.weight_strategies({"ns7": 1.0, "at_val": 0.5, "ns8": 0.8, "cash": 0.0}, ROLES)
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert all(v >= 0 for v in w.values())


def test_outperforming_strategy_overweight():
    w = rotation.weight_strategies({"ns7": 2.0, "at_val": 0.2, "ns8": 1.0, "cash": 0.0}, ROLES)
    assert w["ns7"] > w["ns8"]


def test_negative_momentum_zero_unless_defensive():
    # all-down: defensive floored, others zero, cash dominant
    w = rotation.weight_strategies({"ns7": -1.0, "at_val": -1.0, "ns8": -1.0, "cash": 0.0}, ROLES)
    assert w["ns7"] == 0.0
    assert w["ns8"] == 0.0
    assert w["at_val"] >= config.NSX_DEFENSIVE_FLOOR - 1e-9   # floor kept
    assert w["cash"] > 0.5                                     # risk-off


def test_defensive_floor_true():
    w = rotation.weight_strategies({"ns7": -1.0, "at_val": -1.0, "ns8": -1.0, "cash": 0.0}, ROLES)
    assert abs(w["at_val"] - config.NSX_DEFENSIVE_FLOOR) < 0.02


def test_concentration_cap():
    w = rotation.weight_strategies({"ns7": 5.0, "at_val": 0.1, "ns8": 1.0, "cash": 0.0}, ROLES)
    risky = [v for k, v in w.items() if k != "cash"]
    assert max(risky) <= config.NSX_MAX_STRATEGY_W + 1e-6


def test_no_risky_full_cash():
    w = rotation.weight_strategies({"cash": 0.0}, {"cash": "riskoff"})
    assert w["cash"] == 1.0


def test_missing_stream_fail_open():
    # None momentum (no stream) -> 0, survivors absorb
    w = rotation.weight_strategies({"ns7": 1.0, "at_val": None, "ns8": 1.0, "cash": 0.0}, ROLES)
    assert w["at_val"] >= config.NSX_DEFENSIVE_FLOOR - 1e-9   # defensive floored even on None


def test_deterministic():
    a = rotation.weight_strategies({"ns7": 1.0, "at_val": 0.5, "ns8": 0.8, "cash": 0.0}, ROLES)
    b = rotation.weight_strategies({"ns7": 1.0, "at_val": 0.5, "ns8": 0.8, "cash": 0.0}, ROLES)
    assert a == b


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
