#!/usr/bin/env python3
"""
NS-5 Frontier module tests — synthetic universe, no network.

Run with clean env:
  env -i HOME=$HOME /usr/bin/python3 -m pytest tests/test_frontier.py -q
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import frontier


def _make_closes(n=500, seed=0):
    """Synthetic closes: 4 assets with known risk/return ordering."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n)
    # Asset A: low vol, low return; D: high vol, high return
    specs = {
        "A": {"mu": 0.0002, "sigma": 0.005},
        "B": {"mu": 0.0004, "sigma": 0.008},
        "C": {"mu": 0.0006, "sigma": 0.012},
        "D": {"mu": 0.0009, "sigma": 0.018},
    }
    data = {}
    for tk, s in specs.items():
        rets = rng.normal(s["mu"], s["sigma"], n)
        data[tk] = 100 * np.exp(np.cumsum(rets))
    return pd.DataFrame(data, index=dates)


class TestComputeFrontier:
    def test_shape(self):
        closes = _make_closes()
        fc = frontier.compute_frontier(closes, ["A", "B", "C", "D"])
        assert "error" not in fc
        assert len(fc["frontier"]) >= 10
        assert set(fc["tickers"]) == {"A", "B", "C", "D"}
        assert "gmv" in fc and "max_ret" in fc

    def test_frontier_monotonic_vol(self):
        """Frontier points sorted by vol ascending (left→right)."""
        closes = _make_closes()
        fc = frontier.compute_frontier(closes, ["A", "B", "C", "D"])
        vols = [p["vol"] for p in fc["frontier"]]
        assert vols == sorted(vols)

    def test_frontier_ret_increasing_with_vol(self):
        """On the efficient frontier, higher vol should give higher return."""
        closes = _make_closes()
        fc = frontier.compute_frontier(closes, ["A", "B", "C", "D"])
        rets = [p["ret"] for p in fc["frontier"]]
        assert rets[-1] >= rets[0]

    def test_gmv_inside_asset_range(self):
        """GMV vol should be at or below the lowest single-asset vol."""
        closes = _make_closes()
        fc = frontier.compute_frontier(closes, ["A", "B", "C", "D"])
        min_asset_vol = min(fc["sigma"].values())
        assert fc["gmv"]["vol"] <= min_asset_vol + 1e-4

    def test_max_ret_is_highest_asset(self):
        closes = _make_closes()
        fc = frontier.compute_frontier(closes, ["A", "B", "C", "D"])
        # max_ret ticker must be the argmax of realized mu (sample means are noisy)
        max_tk = max(fc["mu"], key=fc["mu"].get)
        assert fc["max_ret"]["ticker"] == max_tk
        assert fc["max_ret"]["ret"] == max(fc["mu"].values())

    def test_insufficient_tickers(self):
        closes = _make_closes()
        fc = frontier.compute_frontier(closes, ["A"])
        assert "error" in fc

    def test_insufficient_data(self):
        closes = _make_closes(n=30)
        fc = frontier.compute_frontier(closes, ["A", "B"])
        assert "error" in fc


class TestPositionOnFrontier:
    def test_weights_normalized(self):
        closes = _make_closes()
        # 1:1 unnormalized == 50/50 normalized
        pos_raw = frontier.position_on_frontier(
            {"A": 1.0, "B": 1.0}, closes, ["A", "B", "C", "D"])
        pos_norm = frontier.position_on_frontier(
            {"A": 0.5, "B": 0.5}, closes, ["A", "B", "C", "D"])
        assert "error" not in pos_raw
        assert pos_raw["vol"] == pytest.approx(pos_norm["vol"], abs=1e-4)
        assert pos_raw["ret"] == pytest.approx(pos_norm["ret"], abs=1e-4)

    def test_equal_weight_between_assets(self):
        """50/50 of two uncorrelated assets → vol ≤ min(asset vols)
        (diversification effect) and ret between the two asset rets."""
        closes = _make_closes()
        pos = frontier.position_on_frontier({"A": 0.5, "B": 0.5}, closes, ["A", "B"])
        fc = frontier.compute_frontier(closes, ["A", "B"])
        assert pos["vol"] <= min(fc["sigma"]["A"], fc["sigma"]["B"]) + 1e-4
        lo, hi = sorted([fc["mu"]["A"], fc["mu"]["B"]])
        assert lo - 1e-4 <= pos["ret"] <= hi + 1e-4

    def test_single_asset_matches(self):
        closes = _make_closes()
        pos = frontier.position_on_frontier({"A": 1.0}, closes, ["A", "B"])
        fc = frontier.compute_frontier(closes, ["A", "B"])
        assert abs(pos["vol"] - fc["sigma"]["A"]) < 1e-3

    def test_zero_holdings_error(self):
        closes = _make_closes()
        pos = frontier.position_on_frontier({}, closes, ["A", "B"])
        assert "error" in pos


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
