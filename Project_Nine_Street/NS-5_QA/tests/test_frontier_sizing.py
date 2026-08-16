"""tests/test_frontier_sizing.py — R2: frontier-based joint-universe sizing.

Runs under the house 3.9 runtime (the one with sklearn/scipy/pandas), because
NS-5's frontier.py imports sklearn (Ledoit-Wolf). All checks are pure/logic:
no network, no live data.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import frontier_sizing as fs  # noqa: E402


def _synthetic_closes(seed: int = 7):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=300, freq="D")
    return pd.DataFrame({
        "LOW": 100 * np.exp(np.cumsum(rng.normal(0.001, 0.005, 300))),
        "MID": 100 * np.exp(np.cumsum(rng.normal(0.001, 0.010, 300))),
        "HIGH": 100 * np.exp(np.cumsum(rng.normal(0.001, 0.025, 300))),
    }, index=dates)


def test_maxsharpe_weights_sum_to_one_long_only():
    closes = _synthetic_closes()
    res = fs.size_frontier(closes, ["LOW", "MID", "HIGH"], "maxsharpe", rf=0.0)
    w = res["weights"]
    assert abs(sum(w.values()) - 1.0) < 1e-6
    assert all(v >= 0 for v in w.values())       # long-only
    assert set(w.keys()) <= {"LOW", "MID", "HIGH"}


def test_maxsharpe_prefers_low_vol():
    """Max-Sharpe on synthetic data should over-weight the low-vol asset."""
    closes = _synthetic_closes()
    res = fs.size_frontier(closes, ["LOW", "MID", "HIGH"], "maxsharpe", rf=0.0)
    assert res["weights"]["LOW"] > res["weights"]["HIGH"]


def test_gmv_weights_long_only():
    closes = _synthetic_closes()
    res = fs.size_frontier(closes, ["LOW", "MID", "HIGH"], "gmv")
    assert abs(sum(res["weights"].values()) - 1.0) < 1e-6
    assert all(v >= 0 for v in res["weights"].values())


def test_equalweight_fallback():
    closes = _synthetic_closes()
    res = fs.size_frontier(closes, ["LOW", "MID", "HIGH"], "equalweight")
    assert abs(sum(res["weights"].values()) - 1.0) < 1e-6
    assert all(abs(v - 1 / 3) < 1e-9 for v in res["weights"].values())


def test_degenerate_universe_falls_back_to_gmv():
    """A too-small / degenerate universe must not crash — fail-open."""
    closes = _synthetic_closes()
    # single ticker -> GMV returns None -> equal-weight fallback
    res = fs.size_frontier(closes, ["LOW"], "maxsharpe")
    assert "weights" in res
    assert abs(sum(res["weights"].values()) - 1.0) < 1e-6


def test_deterministic():
    """Same inputs -> same weights (reproducible sizing)."""
    closes = _synthetic_closes()
    a = fs.size_frontier(closes, ["LOW", "MID", "HIGH"], "maxsharpe")
    b = fs.size_frontier(closes, ["LOW", "MID", "HIGH"], "maxsharpe")
    assert a["weights"] == b["weights"]


def test_no_ticker_returns_error():
    closes = _synthetic_closes()
    res = fs.size_frontier(closes, ["MISSING", "GONE"], "maxsharpe")
    assert "error" in res


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
