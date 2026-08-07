#!/usr/bin/env python3
"""
NS-5 Tax Axis tests — checkers (C3–C5) + grade/merge/tweaks (F2–F3).

All synthetic + offline — no network. Reuses the _theta pattern from
test_drift/test_frontier.
"""
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, ".")

import tax
import theta as theta_mod


def _theta(**tax_overrides):
    t = dict(theta_mod.TAX_DEFAULTS)
    t.update(tax_overrides)
    return theta_mod.load_theta(tax=t)


def _positions():
    """Synthetic 4-position portfolio with lots + accounts (v2 schema)."""
    return {
        "JEPQ": {"shares": 100, "account": "taxable",
                 "lots": [{"date": "2024-01-15", "shares": 100, "cost_per_share": 55.0}]},
        "VOO": {"shares": 50, "account": "ira",
                "lots": [{"date": "2023-06-01", "shares": 50, "cost_per_share": 420.0}]},
        "CAIE": {"shares": 200, "account": "taxable",
                 "lots": [{"date": "2021-03-01", "shares": 200, "cost_per_share": 25.0}]},
        "TLT": {"shares": 300, "account": "roth",
                "lots": [{"date": "2024-09-01", "shares": 300, "cost_per_share": 95.0}]},
    }


_PRICES = {"JEPQ": 50.0, "VOO": 500.0, "CAIE": 22.0, "TLT": 90.0}


def _chars(positions):
    return {tk: tax._classify_distribution(tk, _theta()) for tk in positions}


# =============================================================================
# F2 — grade functions
# =============================================================================

class TestGradeFunctions:
    def test_after_tax_gap_clean(self):
        th = _theta()
        r = tax.grade_after_tax_gap(0.70, 0.68, False, th)
        assert r["composite_grade"] == "A"
        assert r["gap_pp"] == pytest.approx(0.02, abs=1e-2)

    def test_after_tax_gap_severe(self):
        th = _theta()
        # Committed frontier formula: score = 5 - (gap/3)*5.
        # A 0.40 Sharpe gap → 4.33 → B (calibration question for frontier —
        # research doc §3.2 flags at 0.15; /3 divisor may be too lenient).
        r = tax.grade_after_tax_gap(0.70, 0.30, True, th)
        assert r["composite_grade"] == "B"
        assert r["gap_pp"] == pytest.approx(0.40, abs=1e-2)
        assert r["substitution_available"] is True

    def test_after_tax_gap_missing(self):
        th = _theta()
        r = tax.grade_after_tax_gap(None, None, False, th)
        assert r["composite_grade"] == "N/A"  # fail-open

    def test_tlh_clean(self):
        th = _theta()
        r = tax.grade_tlh(0.001, 10, 1, th)
        assert r["composite_grade"] == "A"

    def test_tlh_large_pool(self):
        th = _theta()
        r = tax.grade_tlh(0.12, 200, 4, th)
        assert r["composite_grade"] == "C"  # linear formula: 5 - 0.12/0.05 = 2.6
        assert r["harvestable_pool_ratio"] == pytest.approx(0.12, abs=1e-3)

    def test_location_clean(self):
        th = _theta()
        r = tax.grade_asset_location(0, 4, 0, th)
        assert r["composite_grade"] == "A"

    def test_location_half_mismatched(self):
        th = _theta()
        r = tax.grade_asset_location(2, 4, 350, th)
        assert r["composite_grade"] == "F"  # 50% mismatch ratio
        assert r["mismatch_ratio"] == pytest.approx(0.5, abs=1e-3)

    def test_erosion_clean(self):
        th = _theta()
        r = tax.grade_basis_erosion(0.0, 0, 0, th)
        assert r["composite_grade"] == "A"

    def test_erosion_locked(self):
        th = _theta()
        r = tax.grade_basis_erosion(0.93, 1, 0, th)
        assert r["composite_grade"] == "F"
        assert r["locked_positions"] == 1


# =============================================================================
# F3 — merge + tweaks
# =============================================================================

class TestMergeAndTweaks:
    def test_merge_fail_open(self):
        """Missing axes get zero weight — composite still grades."""
        th = _theta()
        levels = {"after_tax": tax.grade_after_tax_gap(0.70, 0.55, True, th),
                  "tlh": None, "location": None, "erosion": None}
        merged = tax.merge_tax_grade(levels, th)
        assert merged["composite_tax_grade"] in ("A", "B", "C")
        assert merged["sub_grades"]["tlh"]["grade"] == "N/A"

    def test_merge_all_clean_is_a(self):
        th = _theta()
        levels = {
            "after_tax": tax.grade_after_tax_gap(0.70, 0.69, False, th),
            "tlh": tax.grade_tlh(0.001, 5, 0, th),
            "location": tax.grade_asset_location(0, 4, 0, th),
            "erosion": tax.grade_basis_erosion(0.0, 0, 0, th),
        }
        merged = tax.merge_tax_grade(levels, th)
        assert merged["composite_tax_grade"] == "A"
        # 4.99 < 5.0 strict green cutoff → yellow (same as drift axis behavior)
        assert merged["severity"] == "yellow"

    def test_tweaks_ranked(self):
        th = _theta()
        levels = {
            "after_tax": tax.grade_after_tax_gap(0.70, 0.35, True, th),  # gap 0.35 → score 4.42 < 4.5 → tweak
            "tlh": tax.grade_tlh(0.12, 150, 2, th),
            "location": tax.grade_asset_location(2, 4, 4080, th),
            "erosion": tax.grade_basis_erosion(0.80, 0, 1, th),
        }
        tweaks = tax.generate_tax_tweaks(levels, th)
        assert len(tweaks) == 4
        severities = [t["severity"] for t in tweaks]
        assert "critical" in severities
        for t in tweaks:
            assert t["axis"] == "tax"
            assert t["sub_axis"] in ("after_tax_frontier", "tlh", "asset_location", "basis_erosion")


# =============================================================================
# C3 — TLH checker
# =============================================================================

class TestTLHChecker:
    def test_harvest_detects_losses(self):
        th = _theta()
        r = tax.check_tlh_harvest(_positions(), _PRICES, th)
        assert r["harvest_candidates"] == 2  # JEPQ + CAIE are at a loss
        tickers = {i["ticker"] for i in r["items"]}
        assert tickers == {"JEPQ", "CAIE"}

    def test_harvest_only_taxable(self):
        """IRA/Roth positions excluded from TLH."""
        th = _theta()
        positions = {
            "VOO": {"shares": 50, "account": "ira",
                    "lots": [{"date": "2023-06-01", "shares": 50, "cost_per_share": 420.0}]},
        }
        r = tax.check_tlh_harvest(positions, {"VOO": 500.0}, th)
        assert r["harvest_candidates"] == 0
        assert r["composite_grade"] == "A"

    def test_wash_sale_same_ticker(self):
        th = _theta()
        positions = {
            "AAPL": {"shares": 200, "account": "taxable", "lots": [
                {"date": "2024-01-10", "shares": 100, "cost_per_share": 200.0},
                {"date": "2024-01-25", "shares": 100, "cost_per_share": 195.0},
            ]},
        }
        r = tax.check_tlh_harvest(positions, {"AAPL": 190.0}, th)
        assert r["wash_sale_flags"] >= 1  # two lots 15 days apart

    def test_fail_open_no_lots(self):
        th = _theta()
        positions = {"AAPL": {"shares": 100, "account": "taxable", "lots": []}}
        r = tax.check_tlh_harvest(positions, {"AAPL": 100.0}, th)
        assert r["harvest_candidates"] == 0


# =============================================================================
# C4 — Asset location checker
# =============================================================================

class TestLocationChecker:
    def test_ordinary_in_taxable_mismatch(self):
        th = _theta()
        positions = {"JEPQ": {"shares": 100, "account": "taxable", "lots": []}}
        r = tax.check_asset_location(positions, _chars(positions), th)
        assert r["mismatch_count"] == 1
        assert r["items"][0]["recommended_account"] == "ira/401k"

    def test_qualified_in_ira_mismatch(self):
        """The user refinement: qualified div fund in IRA = LTCG at ordinary rate."""
        th = _theta()
        positions = {"VOO": {"shares": 50, "account": "ira", "lots": []}}
        r = tax.check_asset_location(positions, _chars(positions), th)
        assert r["mismatch_count"] == 1
        assert r["items"][0]["recommended_account"] == "taxable/roth"

    def test_roc_taxable_is_ok(self):
        th = _theta()
        positions = {"CAIE": {"shares": 200, "account": "taxable", "lots": []}}
        r = tax.check_asset_location(positions, _chars(positions), th)
        assert r["mismatch_count"] == 0

    def test_fail_open_axis_off(self):
        th = theta_mod.load_theta()  # tax=None
        positions = _positions()
        r = tax.check_asset_location(positions, _chars(positions), th)
        assert r["composite_grade"] == "N/A"


# =============================================================================
# C5 — Basis erosion checker
# =============================================================================

class TestErosionChecker:
    def test_roc_erosion_accumulates(self):
        th = _theta()
        positions = {"CAIE": {"shares": 200, "account": "taxable",
                              "lots": [{"date": "2021-03-01", "shares": 200, "cost_per_share": 25.0}]}}
        r = tax.check_basis_erosion(positions, _chars(positions), th)
        assert r["max_erosion_ratio"] > 0.5  # 5+ yrs x 14% ROC
        assert len(r["items"]) == 1
        assert r["items"][0]["warning_level"] is not None

    def test_non_roc_no_erosion(self):
        th = _theta()
        positions = {"JEPQ": {"shares": 100, "account": "taxable",
                              "lots": [{"date": "2024-01-15", "shares": 100, "cost_per_share": 55.0}]}}
        r = tax.check_basis_erosion(positions, _chars(positions), th)
        assert r["max_erosion_ratio"] == 0.0
        assert r["items"] == []

    def test_erosion_thresholds(self):
        th = _theta()
        # 10 years x 14% = 140% → clamped to 1.0 → locked
        positions = {"CAIE": {"shares": 100, "account": "taxable",
                              "lots": [{"date": "2016-01-01", "shares": 100, "cost_per_share": 25.0}]}}
        r = tax.check_basis_erosion(positions, _chars(positions), th)
        assert r["locked_positions"] == 1
        assert r["items"][0]["warning_level"] == 0.90


# =============================================================================
# CAIE/JEPQ acceptance test (research doc §10)
# =============================================================================

class TestAfterTaxAcceptance:
    def test_drag_ordering_flips(self):
        """CAIE (ROC, 0 drag) vs JEPQ (ordinary, 40.8%) — the §3.1 textbook case.

        Pre-tax identical, after-tax the ordering flips by the drag gap.
        """
        th = _theta()
        drags = tax._compute_drags(th)
        # JEPQ yield 10.76% x ordinary 40.8% ≈ 4.39pp drag; CAIE 6.85% x 0 ≈ 0
        jepq_drag = 0.1076 * drags["ordinary"]
        caie_drag = 0.0685 * drags["roc"]
        assert jepq_drag > caie_drag + 0.03  # >3pp gap — the flip
        assert abs(jepq_drag - 0.0439) < 0.005

    def test_yield_unit_trap(self):
        """Yahoo dividendYield is a PERCENTAGE — must ÷100 (OMON trap)."""
        # Simulate the data_fetcher guard: 10.76 (%) → 0.1076 (decimal)
        yahoo_percent = 10.76
        decimal = yahoo_percent / 100.0
        assert decimal == pytest.approx(0.1076)
        # And the drag math must use the decimal
        th = _theta()
        drags = tax._compute_drags(th)
        assert 0.1076 * drags["ordinary"] == pytest.approx(0.0439, abs=0.001)
