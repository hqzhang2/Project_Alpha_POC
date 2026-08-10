"""
Portfolio composite × regime enhancer — tests (v3.3.1, synthetic + offline).

Tests drift.compute_portfolio_composite — the pure math behind the
/api/grade portfolio composite (design A — straight line, Hong 2026-08-10):

    base = mean(conc, drift)          # N/A axes excluded
    portfolio = base × enhancer        # enhancer ∈ [0.5, 1.0]
    fail-open: no regime → enhancer 1.0; no base scores → None/N-A
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import drift

# NS-5 theta letter bounds (replicated — theta import needs the common
# bootstrap; bounds are the frozen constant)
LETTER_BOUNDS = [(4.5, "A"), (3.5, "B"), (2.5, "C"), (1.5, "D"), (0.0, "F")]


def _grade_result(conc=None, drift_score=None, regime=None):
    """Build the /api/grade result dict shape consumed by the helper."""
    result = {}
    if conc is not None:
        result["concentration"] = {"composite_concentration_score": conc}
    if drift_score is not None:
        result["drift"] = {"composite_drift_score": drift_score}
    if regime is not None:
        result["regime"] = regime
    return result


class TestComputePortfolioComposite:
    def test_full_path(self):
        """conc 3.16 + drift 4.85 → base 4.0 (B) × 0.89 = 3.56 (B)."""
        r = _grade_result(
            conc=3.16, drift_score=4.85,
            regime={"composite_regime_grade": "B", "enhancer": 0.89})
        out = drift.compute_portfolio_composite(r, LETTER_BOUNDS)
        assert out["base_composite_score"] == 4.0
        assert out["base_composite_grade"] == "B"
        assert out["portfolio_composite_score"] == 3.56
        assert out["portfolio_composite_grade"] == "B"
        assert out["regime_enhancer_applied"] == 0.89

    def test_no_regime_enhancer_1(self):
        """Regime absent → enhancer 1.0, portfolio == base (no penalty)."""
        r = _grade_result(conc=4.0, drift_score=5.0)
        out = drift.compute_portfolio_composite(r, LETTER_BOUNDS)
        assert out["regime_enhancer_applied"] == 1.0
        assert out["base_composite_score"] == 4.5
        assert out["portfolio_composite_score"] == out["base_composite_score"]
        assert out["portfolio_composite_grade"] == "A"

    def test_regime_na_enhancer_1(self):
        """Regime N/A → enhancer 1.0 (fail-open, no penalty)."""
        r = _grade_result(
            conc=3.0, drift_score=3.0,
            regime={"composite_regime_grade": "N/A"})
        out = drift.compute_portfolio_composite(r, LETTER_BOUNDS)
        assert out["regime_enhancer_applied"] == 1.0
        assert out["portfolio_composite_score"] == 3.0

    def test_regime_disabled_error_enhancer_1(self):
        """Regime error dict (disabled) → enhancer 1.0."""
        r = _grade_result(
            conc=3.0, drift_score=3.0,
            regime={"error": "regime axis disabled — configure Θ.regime"})
        out = drift.compute_portfolio_composite(r, LETTER_BOUNDS)
        assert out["regime_enhancer_applied"] == 1.0
        assert out["portfolio_composite_score"] == 3.0

    def test_no_base_scores_fail_open(self):
        """No conc/drift → base None, portfolio None (N/A), never crash."""
        r = _grade_result(regime={"composite_regime_grade": "B", "enhancer": 0.5})
        out = drift.compute_portfolio_composite(r, LETTER_BOUNDS)
        assert out["base_composite_score"] is None
        assert out["base_composite_grade"] == "N/A"
        assert out["portfolio_composite_score"] is None
        assert out["portfolio_composite_grade"] == "N/A"
        assert out["regime_enhancer_applied"] == 0.5

    def test_single_axis_base(self):
        """Only drift present → base = drift alone."""
        r = _grade_result(drift_score=4.0, regime={"composite_regime_grade": "A", "enhancer": 1.0})
        out = drift.compute_portfolio_composite(r, LETTER_BOUNDS)
        assert out["base_composite_score"] == 4.0
        assert out["portfolio_composite_score"] == 4.0

    def test_concentration_na_excluded(self):
        """Concentration N/A (None score) excluded from base mean."""
        r = _grade_result(conc=None, drift_score=4.0,
                          regime={"composite_regime_grade": "B", "enhancer": 0.9})
        out = drift.compute_portfolio_composite(r, LETTER_BOUNDS)
        assert out["base_composite_score"] == 4.0
        assert out["portfolio_composite_score"] == 3.6

    def test_worst_case_floor(self):
        """Regime F (enhancer 0.5) → portfolio = base × 0.5 (floor)."""
        r = _grade_result(
            conc=4.0, drift_score=4.0,
            regime={"composite_regime_grade": "F", "enhancer": 0.5})
        out = drift.compute_portfolio_composite(r, LETTER_BOUNDS)
        assert out["regime_enhancer_applied"] == 0.5
        assert out["portfolio_composite_score"] == 4.0 * 0.5

    def test_letter_boundary(self):
        """Score exactly 3.5 → B (letter_score_bounds ascending boundaries)."""
        r = _grade_result(conc=3.5, drift_score=3.5)
        out = drift.compute_portfolio_composite(r, LETTER_BOUNDS)
        assert out["base_composite_score"] == 3.5
        assert out["base_composite_grade"] == "B"
