#!/usr/bin/env python3
"""
NS-5 Drift Axis tests — checkers (C1–C4) + grade/merge (C5–C6).

All synthetic + offline — no network. Reuses the synthetic-closes pattern
from test_phase4/test_frontier.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import drift
import theta as theta_mod


def _make_closes(n=500, seed=0, tickers=None):
    """Synthetic closes with known vol ordering: A < B < C < D."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n)
    specs = {
        "A": {"mu": 0.0002, "sigma": 0.005},
        "B": {"mu": 0.0004, "sigma": 0.008},
        "C": {"mu": 0.0006, "sigma": 0.012},
        "D": {"mu": 0.0009, "sigma": 0.018},
    }
    data = {}
    for tk in (tickers or ["A", "B", "C", "D"]):
        s = specs.get(tk, {"mu": 0.0005, "sigma": 0.010})
        rets = rng.normal(s["mu"], s["sigma"], n)
        data[tk] = 100 * np.exp(np.cumsum(rets))
    return pd.DataFrame(data, index=dates)


def _make_returns(n=500, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n)
    return pd.Series(rng.normal(0.0004, 0.008, n), index=dates)


def _theta(**over):
    base = theta_mod.THETA_DEFAULTS
    base = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    base.update(over)
    return base


# =========================================================================
# C1 — check_weight_drift
# =========================================================================

class TestWeightDrift:
    def test_within_band_all_clean(self):
        th = _theta(drift_band=0.20)
        # SPY↦Equity, TLT↦Fixed-Income — within band at asset-class level
        r = drift.check_weight_drift({"SPY": 0.12, "TLT": 0.40},
                                     {"SPY": 0.10, "TLT": 0.40}, th)
        assert r["flagged_count"] == 0
        assert r["composite_grade"] == "A"

    def test_outside_band_flagged(self):
        th = _theta(drift_band=0.20)
        # SPY 0.40→Equity=0.40, TLT→Fixed=0.40 (sum 0.80, unnormalized)
        # vs policy SPY 0.10→Equity=0.10, TLT 0.40→Fixed=0.40
        # Equity ratio = |0.40−0.10|/0.10 = 3.0 → flagged
        r = drift.check_weight_drift({"SPY": 0.40, "TLT": 0.40},
                                     {"SPY": 0.10, "TLT": 0.40}, th)
        assert r["flagged_count"] == 1
        items = {i["sector"]: i for i in r["items"]}
        assert items["Equity"]["flagged"] is True
        assert items["Equity"]["ratio"] == pytest.approx(3.0, abs=1e-1)

    def test_no_policy_anchor_flags_position(self):
        th = _theta(drift_band=0.20)
        # AAPL → Sector-Tech → rolls up to Equity with no policy target
        r = drift.check_weight_drift({"AAPL": 0.20}, {}, th)
        assert r["flagged_count"] == 1  # Equity asset class has no anchor

    def test_all_flagged_is_f(self):
        th = _theta(drift_band=0.20)
        # Two tech stocks → Equity vs empty policy → all flagged
        r = drift.check_weight_drift({"AAPL": 0.20, "MSFT": 0.20}, {}, th)
        assert r["composite_grade"] == "F"
        assert r["severity"] == "red"

    def test_empty_inputs(self):
        th = _theta(drift_band=0.20)
        r = drift.check_weight_drift({}, {}, th)
        assert r["flagged_count"] == 0
        assert r["composite_grade"] == "N/A"

    def test_sector_aggregation_works(self):
        """Three equity positions roll into one Equity asset class."""
        th = _theta(drift_band=0.20)
        # AAPL+Sector-Tech→Equity 0.30, SPY→Equity 0.20, TLT→Fixed 0.40
        # Equity total 0.50 vs policy Equity 0.60 → ratio 0.17 < 0.20 → NOT flagged
        # Adding more equity: AAPL 0.40+MSFT 0.40+SPY 0.20=1.0 Eq, TLT 0.40=0.4 FI
        # Equity ratio = |1.0−0.6|/0.6 = 0.67 > 0.20 → flagged, actual=1.0
        r = drift.check_weight_drift({"AAPL": 0.40, "MSFT": 0.40, "SPY": 0.20, "TLT": 0.40},
                                     {"SPY": 0.60, "TLT": 0.40}, th)
        assert r["flagged_count"] >= 1
        items = {i["sector"]: i for i in r["items"]}
        assert items["Equity"]["actual"] == pytest.approx(1.0, abs=1e-3)
        assert items["Equity"]["flagged"] is True


# =========================================================================
# C2 — check_risk_drift
# =========================================================================

class TestRiskDrift:
    def test_constant_returns_no_spike(self):
        th = _theta()
        r = drift.check_risk_drift(_make_returns(), th)
        assert "composite_grade" in r
        assert r["vol_ratio"] <= 1.5

    def test_rising_vol_flagged(self):
        th = _theta()
        # First 300 days calm, last 200 days volatile → trailing vol spikes
        rng = np.random.default_rng(1)
        dates = pd.bdate_range("2024-01-01", periods=500)
        calm = rng.normal(0.0003, 0.004, 300)
        wild = rng.normal(0.0003, 0.02, 200)
        rets = pd.Series(np.concatenate([calm, wild]), index=dates)
        r = drift.check_risk_drift(rets, th)
        assert r["vol_ratio"] > 1.5
        assert r["composite_score"] <= 2.0

    def test_insufficient_data_fail_open(self):
        th = _theta()
        r = drift.check_risk_drift(pd.Series([0.001] * 30), th)
        assert r["composite_grade"] == "N/A"
        assert "error" in r

    def test_var_breach_penalty(self):
        th = _theta(risk_budget={"var_95_limit": -0.05, "cvar_95_limit": -0.10,
                                  "target_vol": 0.14, "vol_spike_sigma": 1.5})
        rng = np.random.default_rng(2)
        dates = pd.bdate_range("2024-01-01", periods=300)
        rets = pd.Series(rng.normal(0.0, 0.03, 300), index=dates)  # heavy daily vol
        r = drift.check_risk_drift(rets, th)
        assert r["var_breach"] is True
        assert r["composite_score"] < 5.0


# =========================================================================
# C3 — check_style_drift
# =========================================================================

class TestStyleDrift:
    def _factors(self, n=500, seed=0):
        rng = np.random.default_rng(seed)
        dates = pd.bdate_range("2024-01-01", periods=n)
        return pd.DataFrame({
            "MKT": rng.normal(0.0004, 0.008, n),
            "SMB": rng.normal(0.0001, 0.005, n),
            "HML": rng.normal(0.0001, 0.005, n),
            "MOM": rng.normal(0.0002, 0.006, n),
            "DUR": rng.normal(0.0000, 0.004, n),
        }, index=dates)

    def test_clean_portfolio_no_flag(self):
        th = _theta(style_tolerance={"factor_sigma": 1.5, "qqq_corr_threshold": 0.90})
        fac = self._factors()
        rng = np.random.default_rng(3)
        dates = fac.index
        # Portfolio = MKT + tiny noise → β_MKT≈1, others ≈0
        rets = pd.Series(fac["MKT"].to_numpy() + rng.normal(0, 0.0001, len(fac)), index=dates)
        policy_beta = {"MKT": 1.0, "SMB": 0.0, "HML": 0.0, "MOM": 0.0, "DUR": 0.0}
        r = drift.check_style_drift(rets, fac, policy_beta, th)
        assert r["composite_grade"] in ("A", "B")
        assert r["flagged_factors"] == []

    def test_factor_shift_flagged(self):
        th = _theta(style_tolerance={"factor_sigma": 1.5, "qqq_corr_threshold": 0.90})
        fac = self._factors(seed=4)
        dates = fac.index
        # Portfolio loads MKT + STRONG HML → HML β far from policy 0
        rng = np.random.default_rng(4)
        rets = pd.Series(fac["MKT"].to_numpy() + 2.0 * fac["HML"].to_numpy() +
                         rng.normal(0, 0.0001, len(fac)), index=dates)
        policy_beta = {"MKT": 1.0, "SMB": 0.0, "HML": 0.0, "MOM": 0.0, "DUR": 0.0}
        r = drift.check_style_drift(rets, fac, policy_beta, th)
        assert any(f["flagged"] for f in r["factor_deviations"])
        assert r["composite_score"] < 5.0

    def test_missing_data_fail_open(self):
        th = _theta()
        r = drift.check_style_drift(None, pd.DataFrame(), {}, th)
        assert r["composite_grade"] == "N/A"

    def test_deterministic(self):
        th = _theta(style_tolerance={"factor_sigma": 1.5, "qqq_corr_threshold": 0.90})
        fac = self._factors(seed=5)
        dates = fac.index
        rets = pd.Series(fac["MKT"].to_numpy(), index=dates)
        pb = {"MKT": 1.0, "SMB": 0.0, "HML": 0.0, "MOM": 0.0, "DUR": 0.0}
        r1 = drift.check_style_drift(rets, fac, pb, th)
        r2 = drift.check_style_drift(rets, fac, pb, th)
        assert r1["composite_score"] == r2["composite_score"]
        assert [f["flagged"] for f in r1["factor_deviations"]] == \
               [f["flagged"] for f in r2["factor_deviations"]]


# =========================================================================
# C4 — check_frontier_drift
# =========================================================================

class TestFrontierDrift:
    def test_stable_frontier_clean(self):
        th = _theta()
        # Repeat the same 250-day block twice → both halves statistically
        # identical → no drift. (Two random halves would differ by sampling
        # noise and the checker honestly flags it.)
        base = _make_closes(n=250, seed=6)
        closes = pd.concat([base, base])
        holdings = {"A": 0.5, "B": 0.5}
        r = drift.check_frontier_drift(closes, holdings, {"A": 0.5, "B": 0.5}, th)
        assert r["composite_grade"] in ("A", "B")
        assert r["tangency_shift"] < 0.3  # identical halves → near-zero shift

    def test_insufficient_closes_fail_open(self):
        th = _theta()
        closes = _make_closes(n=50, seed=7)
        r = drift.check_frontier_drift(closes, {"A": 0.5, "B": 0.5},
                                       {"A": 0.5, "B": 0.5}, th)
        assert r["composite_grade"] == "N/A"

    def test_returns_grade_structure(self):
        th = _theta()
        closes = _make_closes(seed=8)
        r = drift.check_frontier_drift(closes, {"A": 0.5, "B": 0.5},
                                       {"A": 0.5, "B": 0.5}, th)
        assert "composite_grade" in r and "composite_score" in r
        assert "sharpe_long_run" in r and "sharpe_trailing" in r
        assert "tangency_shift" in r


# =========================================================================
# C6 — merge + tweaks
# =========================================================================

class TestDriftMerge:
    def _all_a_levels(self):
        th = _theta()
        return {
            "weight": drift.grade_weight_drift(0, 10, th),
            "risk": drift.grade_risk_drift(0.9, False, False, th),
            "style": drift.grade_style_drift(
                [{"factor": "MKT", "delta_sigma": 0.1, "flagged": False}], 0.50, th),
            "frontier": drift.grade_frontier_drift(0.05, 0.08, False, th),
        }

    def test_merge_all_clean(self):
        th = _theta()
        m = drift.merge_drift_grade(self._all_a_levels(), th)
        assert m["composite_drift_grade"] == "A"
        assert m["composite_drift_score"] == pytest.approx(4.85)
        assert set(m["sub_grades"].keys()) == {"weight", "risk", "style", "frontier"}

    def test_merge_missing_axis_fail_open(self):
        th = _theta()
        levels = self._all_a_levels()
        del levels["frontier"]
        m = drift.merge_drift_grade(levels, th)
        assert m["sub_grades"]["frontier"]["grade"] == "N/A"
        assert m["composite_drift_grade"]  # still computable

    def test_merge_mixed_drift_lower(self):
        th = _theta()
        levels = {
            "weight": drift.grade_weight_drift(3, 10, th),
            "risk": drift.grade_risk_drift(1.3, False, False, th),
            "style": drift.grade_style_drift(
                [{"factor": "HML", "delta_sigma": 3.1, "flagged": True}], 0.50, th),
            "frontier": drift.grade_frontier_drift(0.05, 0.08, False, th),
        }
        m = drift.merge_drift_grade(levels, th)
        assert m["composite_drift_score"] < 4.0
        assert len(drift.generate_drift_tweaks(levels, th)) > 0

    def test_tweaks_clean_none(self):
        th = _theta()
        assert drift.generate_drift_tweaks(self._all_a_levels(), th) == []

    def test_tweak_structure(self):
        th = _theta()
        levels = {
            "weight": drift.grade_weight_drift(3, 10, th),
            "risk": drift.grade_risk_drift(1.3, False, False, th),
            "style": drift.grade_style_drift(
                [{"factor": "HML", "delta_sigma": 3.1, "flagged": True}], 0.50, th),
            "frontier": drift.grade_frontier_drift(0.05, 0.08, False, th),
        }
        tweaks = drift.generate_drift_tweaks(levels, th)
        assert tweaks
        for tk in tweaks:
            assert "axis" in tk and "level" in tk and "severity" in tk
            assert "recommended_action" in tk and "rationale" in tk
        # sorted: critical first
        order = {"critical": 0, "high": 1, "medium": 2}
        sevs = [order[t["severity"]] for t in tweaks]
        assert sevs == sorted(sevs)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
