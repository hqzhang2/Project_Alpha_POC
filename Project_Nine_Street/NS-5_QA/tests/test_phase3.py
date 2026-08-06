#!/usr/bin/env python3
"""
NS-5 Phase 3 unit tests — sector weights, effective-N, tail correlation.

Run with clean env (house rule):
  env -i HOME=$HOME /usr/bin/python3 -m pytest tests/test_phase3.py -q

No network — synthetic data only.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import checks
import config
import theta as theta_mod


def _make_theta(**overrides):
    theta = theta_mod.load_theta()
    theta.update(overrides)
    return theta


# ---------------------------------------------------------------------------
# 3.2 Sector weights
# ---------------------------------------------------------------------------

class TestSectorWeights:
    def test_map_ticker_to_sector_known(self):
        theta = _make_theta()
        assert checks.map_ticker_to_sector("AAPL", theta) == "Sector-Tech"
        assert checks.map_ticker_to_sector("XOM", theta) == "Sector-Energy"
        assert checks.map_ticker_to_sector("SPY", theta) == "Equity-Large"

    def test_map_ticker_to_sector_unknown(self):
        theta = _make_theta()
        assert checks.map_ticker_to_sector("ZZZZ", theta) == "Unknown"

    def test_worst_of_rule_single_bad_sector(self):
        """One sector at 60% (cap 30%, ratio 2.0) → F even if others fine."""
        theta = _make_theta(max_sector_pct=0.30)
        holdings = {"AAPL": 0.30, "MSFT": 0.30, "XOM": 0.10, "JPM": 0.10,
                     "UNH": 0.10, "PFE": 0.10}
        result = checks.grade_sector_weights(holdings, theta)
        # Tech = 0.60 → ratio 2.0 → F
        assert result["sector_details"]["Sector-Tech"]["grade"] == "F"
        assert result["composite_grade"] == "F"  # worst-of
        assert result["composite_score"] == 1

    def test_within_cap_all_a(self):
        theta = _make_theta(max_sector_pct=0.30)
        holdings = {"AAPL": 0.15, "XOM": 0.15, "JPM": 0.15, "UNH": 0.15,
                     "PG": 0.15, "CAT": 0.15, "LIN": 0.10}
        result = checks.grade_sector_weights(holdings, theta)
        assert result["composite_grade"] == "A"
        assert all(d["flagged"] is False for d in result["sector_details"].values())

    def test_unknown_tickers_listed(self):
        theta = _make_theta()
        holdings = {"AAPL": 0.5, "ZZZZ": 0.5}
        result = checks.grade_sector_weights(holdings, theta)
        assert "ZZZZ" in result["unknown_tickers"]
        # Unknown bucket gets graded too — must not crash
        assert "Unknown" in result["sector_details"]

    def test_sector_weights_sum(self):
        theta = _make_theta()
        holdings = {"AAPL": 0.2, "MSFT": 0.2, "XOM": 0.3, "JPM": 0.3}
        result = checks.grade_sector_weights(holdings, theta)
        assert abs(sum(result["sector_weights"].values()) - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# 3.3 Effective N
# ---------------------------------------------------------------------------

class TestEffectiveN:
    def test_single_position_f(self):
        theta = _make_theta(effective_n_floor=12)
        result = checks.grade_effective_n({"AAPL": 1.0}, theta)
        assert result["effective_n"] == pytest.approx(1.0)
        assert result["composite_grade"] == "F"
        assert result["composite_score"] == pytest.approx(round(5.0 / 12, 2))  # 0.42

    def test_floor_reached_a(self):
        theta = _make_theta(effective_n_floor=12)
        holdings = {f"T{i}": 1.0 / 12 for i in range(12)}
        result = checks.grade_effective_n(holdings, theta)
        assert result["effective_n"] == pytest.approx(12.0)
        assert result["composite_grade"] == "A"
        assert result["composite_score"] == pytest.approx(5.0)

    def test_linear_midpoint(self):
        theta = _make_theta(effective_n_floor=12)
        # 6 equal positions → N_eff 6 → score = 5*(6/12) = 2.5 → C
        holdings = {f"T{i}": 1.0 / 6 for i in range(6)}
        result = checks.grade_effective_n(holdings, theta)
        assert result["effective_n"] == pytest.approx(6.0)
        assert result["composite_score"] == pytest.approx(2.5)
        assert result["composite_grade"] == "C"

    def test_capped_at_floor(self):
        theta = _make_theta(effective_n_floor=12)
        holdings = {f"T{i}": 1.0 / 20 for i in range(20)}
        result = checks.grade_effective_n(holdings, theta)
        assert result["composite_score"] == pytest.approx(5.0)  # capped
        assert result["composite_grade"] == "A"


# ---------------------------------------------------------------------------
# 3.4 Tail correlation
# ---------------------------------------------------------------------------

def _make_tail_closes(seed=0, n_days=300, n_periods=2):
    """Two 'regimes': 150 calm days, 150 tail days. All tickers correlated."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n_days)
    base = rng.normal(0, 0.005, n_days)
    # 5 tickers all driven by same common factor
    data = {}
    for i, tk in enumerate(["A", "B", "C", "D", "E"]):
        data[tk] = 100 * np.exp(np.cumsum(base + rng.normal(0, 0.001, n_days)))
    return pd.DataFrame(data, index=dates)


class TestTailCorrelation:
    def test_highly_correlated_tail_flag(self):
        theta = _make_theta(tail_pctile=5, tail_corr_threshold=0.7, top_n_for_tail=5)
        closes = _make_tail_closes()
        holdings = {"A": 0.3, "B": 0.2, "C": 0.2, "D": 0.15, "E": 0.15}
        result = checks.grade_tail_correlation(holdings, theta, closes=closes)
        # All 5 tickers driven by the same factor → tail correlations ~1.0
        assert len(result["flagged_pairs"]) >= 4  # C(5,2)=10 pairs, most flagged
        assert result["composite_grade"] == "C"   # 2+ flagged pairs → C

    def test_uncorrelated_no_flag(self):
        theta = _make_theta(tail_pctile=5, tail_corr_threshold=0.7, top_n_for_tail=5)
        rng = np.random.default_rng(1)
        dates = pd.bdate_range("2024-01-01", periods=300)
        data = {tk: 100 * np.exp(np.cumsum(rng.normal(0, 0.005, 300)))
                for tk in ["A", "B", "C", "D", "E"]}
        closes = pd.DataFrame(data, index=dates)
        holdings = {"A": 0.3, "B": 0.2, "C": 0.2, "D": 0.15, "E": 0.15}
        result = checks.grade_tail_correlation(holdings, theta, closes=closes)
        assert len(result["flagged_pairs"]) <= 1
        assert result["composite_grade"] in ("A", "B")

    def test_missing_data_error_handling(self):
        theta = _make_theta()
        # Closes without the requested tickers → graceful error, not crash
        closes = pd.DataFrame({"ZZZ": [1.0, 2.0]})
        result = checks.grade_tail_correlation({"AAPL": 1.0}, theta, closes=closes)
        assert "error" in result or result["positions_checked"] == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
