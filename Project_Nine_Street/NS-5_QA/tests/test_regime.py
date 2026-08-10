"""
NS-5 Regime Axis Checkers — tests (synthetic + offline only).

Tests for:
  - check_frontier_shift: GMV mix distance
  - check_tangency: Sharpe ratio degradation
  - check_policy_gap: policy vs current tangency distance
  - check_corr_structure: SPY/TLT 60d corr
  - run_regime_checkers: full pipeline with monkeypatched history
  - Fail-open: insufficient data → N/A grade

ALL tests are synthetic — never hit FRED/Yahoo/network.
Run: pytest tests/test_regime.py -q (inside NS-5_QA/)
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import theta as theta_mod
from regime_checkers import (
    check_frontier_shift, check_tangency, check_policy_gap,
    check_corr_structure, run_regime_checkers,
    _gmv_weights, _tangency_weights, _tangency_sharpe,
)


# ═══════════════════════════════════════════════════════════════════════
# Synthetic data builders
# ═══════════════════════════════════════════════════════════════════════

def _make_returns(days=500, n_assets=3, seed=42, drift=None):
    """Synthetic daily log-returns with deterministic seed."""
    rng = np.random.RandomState(seed)
    cols = ["A", "B", "C"][:n_assets]
    mu = np.zeros(n_assets)
    if drift is not None:
        mu = np.asarray(drift, dtype=float)[:n_assets]
    rets = rng.randn(days, n_assets) * 0.01 + mu / 252
    idx = pd.date_range("2024-01-01", periods=days, freq="B")
    return pd.DataFrame(rets, index=idx, columns=cols)


def _make_closes(days=500, seed=42):
    """Synthetic closes with SPY/TLT for correlation checks."""
    rng = np.random.RandomState(seed)
    idx = pd.date_range("2024-01-01", periods=days, freq="B")
    spy = 400 * np.exp(np.cumsum(rng.randn(days) * 0.01))
    tlt = 100 * np.exp(np.cumsum(rng.randn(days) * 0.008))
    return pd.DataFrame({"SPY": spy, "TLT": tlt}, index=idx)


def _make_history(regime="R1", days=500):
    """Synthetic regime history (all one regime by default)."""
    idx = pd.date_range("2024-01-01", periods=days, freq="B")
    return pd.DataFrame({"regime": [regime] * days}, index=idx)


# ═══════════════════════════════════════════════════════════════════════
# Closed-form helpers
# ═══════════════════════════════════════════════════════════════════════

class TestClosedForm:
    def test_gmv_weights_sum_to_one(self):
        rets = _make_returns()
        w = _gmv_weights(rets)
        assert w
        # weights rounded to 4dp each → sum within rounding tolerance
        assert abs(sum(w.values()) - 1.0) < 5e-4
        assert all(v >= 0 for v in w.values())

    def test_gmv_empty_on_short_history(self):
        rets = _make_returns(days=10)
        assert _gmv_weights(rets) == {}

    def test_tangency_weights_sum_to_one(self):
        rets = _make_returns(drift=[0.08, 0.04, 0.02])
        w = _tangency_weights(rets)
        assert w
        assert abs(sum(w.values()) - 1.0) < 1e-6

    def test_tangency_sharpe_positive_with_drift(self):
        rets = _make_returns(drift=[0.08, 0.04, 0.02])
        s = _tangency_sharpe(rets)
        assert s is not None and s > 0

    def test_tangency_sharpe_none_on_short(self):
        assert _tangency_sharpe(_make_returns(days=10)) is None


# ═══════════════════════════════════════════════════════════════════════
# Checkers
# ═══════════════════════════════════════════════════════════════════════

class TestCheckFrontierShift:
    def test_clean_case_same_returns(self):
        rets = _make_returns()
        r = check_frontier_shift(rets, rets)
        assert r["gmv_all"] and r["gmv_current"]
        assert r["gmv_all"] == r["gmv_current"]

    def test_fail_open_none(self):
        r = check_frontier_shift(None, None)
        assert r == {"gmv_all": {}, "gmv_current": {}}


class TestCheckTangency:
    def test_clean_case_same(self):
        rets = _make_returns(drift=[0.06, 0.03, 0.01])
        r = check_tangency(rets, rets)
        assert r["sharpe_all"] is not None
        assert r["sharpe_current"] is not None
        assert abs(r["sharpe_all"] - r["sharpe_current"]) < 1e-6

    def test_degraded_current_regime(self):
        """Current regime returns are mostly noise → lower Sharpe."""
        rets_all = _make_returns(drift=[0.06, 0.03, 0.01], seed=1)
        rets_cur = _make_returns(drift=[0.0, 0.0, 0.0], seed=2)
        r = check_tangency(rets_all, rets_cur)
        assert r["sharpe_current"] < r["sharpe_all"]

    def test_fail_open_none(self):
        r = check_tangency(None, None)
        assert r == {"sharpe_all": None, "sharpe_current": None}


class TestCheckPolicyGap:
    def test_policy_near_tangency(self):
        """Policy close to tangency → small distance (grade A)."""
        rets = _make_returns(drift=[0.06, 0.03, 0.01])
        tang = _tangency_weights(rets)
        r = check_policy_gap(tang, rets)  # policy == tangency
        assert r["policy_weights"]
        assert r["current_tangency_weights"]
        # Distance should be ~0 (policy IS the tangency)
        keys = set(r["policy_weights"]) | set(r["current_tangency_weights"])
        d = sum((r["policy_weights"].get(k, 0) - r["current_tangency_weights"].get(k, 0)) ** 2
                for k in keys) ** 0.5
        assert d < 0.05

    def test_fail_open_empty_policy(self):
        r = check_policy_gap({}, _make_returns())
        assert r["policy_weights"] == {}

    def test_fail_open_none_rets(self):
        r = check_policy_gap({"A": 1.0}, None)
        assert r["current_tangency_weights"] == {}


class TestCheckCorrStructure:
    def test_returns_corr(self):
        closes = _make_closes()
        r = check_corr_structure(closes)
        assert r["stock_bond_corr"] is not None
        assert -1.0 <= r["stock_bond_corr"] <= 1.0

    def test_fail_open_no_tlt(self):
        closes = _make_closes().drop(columns=["TLT"])
        r = check_corr_structure(closes)
        assert r == {"stock_bond_corr": None}

    def test_fail_open_none(self):
        assert check_corr_structure(None) == {"stock_bond_corr": None}


# ═══════════════════════════════════════════════════════════════════════
# Full pipeline (monkeypatched history — no network)
# ═══════════════════════════════════════════════════════════════════════

class TestRunRegimeCheckers:
    def _mock_history(self, monkeypatch, regime="R1"):
        import common.regime_store as store_mod
        import common.regime_pipeline as pipe_mod

        def _fake_query(days=750):
            return _make_history(regime)

        def _fake_pipeline(days_back=750):
            return _make_history(regime)

        monkeypatch.setattr(store_mod, "query_window", _fake_query)
        monkeypatch.setattr(pipe_mod, "run_regime_pipeline", _fake_pipeline)
        # Also patch regime_checkers' reference
        import regime_checkers
        monkeypatch.setattr(regime_checkers, "_get_regime_history",
                            lambda theta, force_refresh=False: _make_history(regime))

    def test_full_pipeline_clean(self, monkeypatch):
        self._mock_history(monkeypatch, "R1")
        closes = _make_closes()
        theta = theta_mod.load_theta()
        result = run_regime_checkers(closes=closes,
                                     policy_weights={"SPY": 0.6, "TLT": 0.4},
                                     theta=theta)
        assert result["composite_regime_grade"] in ("A", "B")
        assert "levels" in result
        assert "frontier_shift" in result["levels"]
        assert "tweaks" in result

    def test_fail_open_disabled_axis(self, monkeypatch):
        self._mock_history(monkeypatch)
        theta = theta_mod.load_theta()
        theta["regime"] = None
        result = run_regime_checkers(closes=_make_closes(), theta=theta)
        assert result["composite_regime_grade"] == "N/A"
        assert "error" in result

    def test_fail_open_no_history(self, monkeypatch):
        import common.regime_store as store_mod
        import common.regime_pipeline as pipe_mod
        import regime_checkers
        monkeypatch.setattr(store_mod, "query_window", lambda days=750: pd.DataFrame())
        monkeypatch.setattr(pipe_mod, "run_regime_pipeline", lambda days_back=750: pd.DataFrame())
        monkeypatch.setattr(regime_checkers, "_get_regime_history",
                            lambda theta, force_refresh=False: pd.DataFrame())
        result = run_regime_checkers(closes=_make_closes(), theta=theta_mod.load_theta())
        assert result["composite_regime_grade"] == "N/A"

    def test_fail_open_no_closes(self, monkeypatch):
        self._mock_history(monkeypatch)
        result = run_regime_checkers(closes=None, theta=theta_mod.load_theta())
        assert result["composite_regime_grade"] == "N/A"

    def test_pipeline_grade_domain_sanity(self, monkeypatch):
        """Mid-case must NOT always grade A (grade-bounds domain pitfall)."""
        self._mock_history(monkeypatch, "R2")
        # Highly drifted universe: current regime returns are noise
        import regime_checkers
        closes = _make_closes(seed=7)
        theta = theta_mod.load_theta()
        # Force the current-regime returns to be very different by
        # monkeypatching filter_regime_returns to return noise returns
        noise = _make_returns(days=500, n_assets=2, seed=99, drift=[0.0, 0.0])
        noise.columns = ["SPY", "TLT"]
        import common.regime_model as rm
        monkeypatch.setattr(rm, "filter_regime_returns",
                            lambda rets, hist, regime, min_days=60: noise)
        result = run_regime_checkers(closes=closes, policy_weights={"SPY": 0.6, "TLT": 0.4},
                                     theta=theta)
        # With tangency crushed to noise, composite should NOT be A
        assert result["composite_regime_grade"] != "A"
