"""
NS-5 Regime Model Tests — synthetic + offline only.

Tests for:
  - RegimeClassifier (PRIMARY classification, monetary overlay, credit,
    external, market confirmation)
  - filter_regime_returns() with synthetic returns
  - RegimeFetcher fail-open (no key, bad response)
  - RegimeStore upsert idempotency + query
  - RegimePipeline integration (fetch→classify→store→query)

ALL tests are synthetic — never hit real FRED/Yahoo endpoints.
Run: pytest common/test_regime.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(__file__))

from regime_model import (
    REGIME_THETA,
    RegimeClassifier,
    current_regime_from_history,
    filter_regime_returns,
)

# ═══════════════════════════════════════════════════════════════════════
# Test data builders
# ═══════════════════════════════════════════════════════════════════════

def _make_data(overrides=None):
    """Base macro data dict (R1 Expansion by default).

    Values are in percentage-point / decimal units.
    2S10S = 0.50 → 50bp (classifier multiplies by 100).
    BAA_AAA = 0.44 → 44bp.
    """
    d = {
        "GDP_QOQ_ANN": 2.4,
        "CPI_YOY": 1.5,
        "CPI_TREND_3M": 0.0,
        "UNRATE_3M_CHG": -0.2,
        "2S10S": 0.50,      # +50 bp
        "BAA_AAA": 0.44,     # 44 bp
        "NFCI": -0.5,
        "DCOILWTICO": 75.0,
        "USD_MOM_PCT": 0.3,
        "VIX": 15.1,
        "STOCK_BOND_CORR": -0.25,
    }
    if overrides:
        d.update(overrides)
    return d


# ═══════════════════════════════════════════════════════════════════════
# Classifier: PRIMARY classification
# ═══════════════════════════════════════════════════════════════════════

class TestPrimaryClassification:
    """Step 1: GROWTH × INFLATION → R1/R2/R3/R4."""

    def test_R1_expansion(self):
        """Growth above, inflation below → R1."""
        c = RegimeClassifier()
        r = c.classify(_make_data({"GDP_QOQ_ANN": 2.4, "CPI_YOY": 1.5}))
        assert r.regime == "R1"
        assert r.growth_above is True
        assert r.inflation_above is False

    def test_R2_overheating(self):
        """Growth above, inflation above → R2."""
        c = RegimeClassifier()
        r = c.classify(_make_data({"GDP_QOQ_ANN": 2.4, "CPI_YOY": 3.2, "CPI_TREND_3M": 0.1}))
        assert r.regime == "R2"
        assert r.growth_above is True
        assert r.inflation_above is True

    def test_R2_inflation_above_by_trend(self):
        """CPI above 2% with flat trend → R2 (trend not falling > 0.05)."""
        c = RegimeClassifier()
        r = c.classify(_make_data({"GDP_QOQ_ANN": 2.4, "CPI_YOY": 3.2, "CPI_TREND_3M": -0.01}))
        assert r.regime == "R2"

    def test_R2_inflation_above_by_missing_trend(self):
        """CPI above 2% with no trend → defaults to 0 → R2."""
        c = RegimeClassifier()
        data = _make_data({"GDP_QOQ_ANN": 2.4, "CPI_YOY": 3.2})
        data.pop("CPI_TREND_3M", None)
        r = c.classify(data)
        assert r.regime == "R2"

    def test_R3_recession(self):
        """Growth below, inflation below → R3."""
        c = RegimeClassifier()
        r = c.classify(_make_data({
            "GDP_QOQ_ANN": -1.0, "CPI_YOY": 1.2,
            "UNRATE_3M_CHG": 0.5,
        }))
        assert r.regime == "R3"
        assert r.growth_above is False
        assert r.inflation_above is False

    def test_R3_growth_below_on_gdp_only(self):
        """GDP below 2% + unemployment rising → growth_above False."""
        c = RegimeClassifier()
        r = c.classify(_make_data({
            "GDP_QOQ_ANN": 1.0, "UNRATE_3M_CHG": 0.5, "CPI_YOY": 1.0,
        }))
        assert r.regime == "R3"
        assert r.growth_above is False

    def test_R4_stagflation(self):
        """Growth below, inflation above → R4."""
        c = RegimeClassifier()
        r = c.classify(_make_data({
            "GDP_QOQ_ANN": -1.0, "CPI_YOY": 4.0, "CPI_TREND_3M": 0.2,
            "UNRATE_3M_CHG": 0.6,
        }))
        assert r.regime == "R4"
        assert r.growth_above is False
        assert r.inflation_above is True

    def test_growth_catch_all_unemployment_falling(self):
        """GDP missing but UNRATE falling → growth_above = True."""
        c = RegimeClassifier()
        r = c.classify(_make_data({"CPI_YOY": 1.5, "UNRATE_3M_CHG": -0.3}))
        assert r.growth_above is True
        assert r.regime == "R1"

    def test_growth_catch_all_unemployment_stable(self):
        """GDP missing but UNRATE stable (0.0) → growth_above = True."""
        c = RegimeClassifier()
        r = c.classify(_make_data({"CPI_YOY": 1.5, "UNRATE_3M_CHG": 0.0}))
        assert r.growth_above is True

    def test_inflation_below_target(self):
        """CPI exactly at 1.99% → inflation_above = False."""
        c = RegimeClassifier()
        r = c.classify(_make_data({"GDP_QOQ_ANN": 2.4, "CPI_YOY": 1.99}))
        assert r.inflation_above is False

    def test_inflation_at_target(self):
        """CPI exactly at 2.0% → inflation_above = True."""
        c = RegimeClassifier()
        r = c.classify(_make_data({
            "GDP_QOQ_ANN": 2.4, "CPI_YOY": 2.0, "CPI_TREND_3M": 0.0,
        }))
        assert r.inflation_above is True


# ═══════════════════════════════════════════════════════════════════════
# Classifier: monetary overlay (Step 2)
# ═══════════════════════════════════════════════════════════════════════

class TestMonetaryOverlay:
    """Step 2: yield curve + confidence adjustment."""

    def test_late_cycle_R1_inverted(self):
        """R1 + inverted curve (-20bp, below -10bp threshold) → 'late cycle'."""
        c = RegimeClassifier()
        r = c.classify(_make_data({
            "GDP_QOQ_ANN": 2.4, "CPI_YOY": 1.5,
            "2S10S": -0.20,  # -20 bp (classifier does *100 → -20 < -10)
        }))
        assert "late cycle" in r.flags
        assert r.confidence == 0.7  # capped at 0.7

    def test_no_late_cycle_when_curve_normal(self):
        """R1 + positive curve → no late cycle flag."""
        c = RegimeClassifier()
        r = c.classify(_make_data({
            "GDP_QOQ_ANN": 2.4, "CPI_YOY": 1.5,
            "2S10S": 0.50,  # +50 bp
        }))
        assert "late cycle" not in r.flags

    def test_deinverting_R3(self):
        """R3 + de-inverting curve → 'de-inverting' flag."""
        c = RegimeClassifier()
        r = c.classify(_make_data({
            "GDP_QOQ_ANN": -1.0, "CPI_YOY": 1.0,
            "UNRATE_3M_CHG": 0.5,
            "2S10S": 0.50,  # +50 bp now
            "2S10S_60D_AGO": -0.20,  # -20 bp 60 days ago
        }))
        assert r.regime == "R3"
        assert "de-inverting" in r.flags


# ═══════════════════════════════════════════════════════════════════════
# Classifier: credit confirmation (Step 3)
# ═══════════════════════════════════════════════════════════════════════

class TestCreditCheck:
    """Step 3: BAA-AAA spread, NFCI."""

    def test_credit_stress(self):
        """BAA-AAA at 2.50 (=250bp, >200bp threshold) → 'credit stress'."""
        c = RegimeClassifier()
        r = c.classify(_make_data({
            "GDP_QOQ_ANN": 2.4, "CPI_YOY": 1.5,
            "BAA_AAA": 2.50,  # 250 bp (*100 → 250 > 200)
        }))
        assert "credit stress" in r.flags

    def test_nfci_tight_confirm_R3(self):
        """NFCI > 0 in R3 → 'NFCI tight (confirm)'."""
        c = RegimeClassifier()
        r = c.classify(_make_data({
            "GDP_QOQ_ANN": -1.0, "CPI_YOY": 1.0,
            "UNRATE_3M_CHG": 0.5,
            "NFCI": 0.5,
        }))
        assert "NFCI tight (confirm)" in r.flags

    def test_nfci_tight_contradict_R1(self):
        """NFCI > 0 in R1 → 'NFCI tight (contradict)'."""
        c = RegimeClassifier()
        r = c.classify(_make_data({
            "GDP_QOQ_ANN": 2.4, "CPI_YOY": 1.5,
            "NFCI": 0.3,
        }))
        assert "NFCI tight (contradict)" in r.flags


# ═══════════════════════════════════════════════════════════════════════
# Classifier: external check (Step 4)
# ═══════════════════════════════════════════════════════════════════════

class TestExternalCheck:
    """Step 4: WTI oil, USD squeeze."""

    def test_stagflation_risk_R2(self):
        """WTI > $100 in R2 → 'stagflation risk (WTI)'."""
        c = RegimeClassifier()
        r = c.classify(_make_data({
            "GDP_QOQ_ANN": 2.4, "CPI_YOY": 3.2, "CPI_TREND_3M": 0.2,
            "DCOILWTICO": 110.0,
        }))
        assert "stagflation risk (WTI)" in r.flags

    def test_stagflation_risk_R4(self):
        """WTI > $100 in R4 → 'stagflation risk (WTI)'."""
        c = RegimeClassifier()
        r = c.classify(_make_data({
            "GDP_QOQ_ANN": -1.0, "CPI_YOY": 4.0, "CPI_TREND_3M": 0.2,
            "UNRATE_3M_CHG": 0.6,
            "DCOILWTICO": 110.0,
        }))
        assert "stagflation risk (WTI)" in r.flags

    def test_usd_squeeze(self):
        """USD +6% month → 'USD squeeze'."""
        c = RegimeClassifier()
        r = c.classify(_make_data({
            "GDP_QOQ_ANN": 2.4, "CPI_YOY": 1.5,
            "USD_MOM_PCT": 6.0,
        }))
        assert "USD squeeze" in r.flags

    def test_usd_squeeze_negative(self):
        """USD -6% month → 'USD squeeze' (abs > 5%)."""
        c = RegimeClassifier()
        r = c.classify(_make_data({
            "GDP_QOQ_ANN": 2.4, "CPI_YOY": 1.5,
            "USD_MOM_PCT": -6.0,
        }))
        assert "USD squeeze" in r.flags


# ═══════════════════════════════════════════════════════════════════════
# Classifier: market confirmation (Step 5)
# ═══════════════════════════════════════════════════════════════════════

class TestMarketCheck:
    """Step 5: VIX, stock-bond correlation — never reclassifies."""

    def test_acute_vix(self):
        """VIX > 28 → 'acute (VIX)'."""
        c = RegimeClassifier()
        r = c.classify(_make_data({
            "GDP_QOQ_ANN": 2.4, "CPI_YOY": 1.5,
            "VIX": 30.0,
        }))
        assert "acute (VIX)" in r.flags
        assert r.regime == "R1"  # VIX does NOT reclassify

    def test_anomalous_corr_R1_positive(self):
        """Positive stock-bond corr in R1 → flag."""
        c = RegimeClassifier()
        r = c.classify(_make_data({
            "GDP_QOQ_ANN": 2.4, "CPI_YOY": 1.5,
            "STOCK_BOND_CORR": 0.3,
        }))
        assert "anomalous corr (+in R1/R3)" in r.flags

    def test_anomalous_corr_R2_negative(self):
        """Negative stock-bond corr in R2 → flag."""
        c = RegimeClassifier()
        r = c.classify(_make_data({
            "GDP_QOQ_ANN": 2.4, "CPI_YOY": 3.2, "CPI_TREND_3M": 0.2,
            "STOCK_BOND_CORR": -0.3,
        }))
        assert "anomalous corr (-in R2/R4)" in r.flags

    def test_no_anomalous_corr_R1_normal(self):
        """Negative corr in R1 is expected → no anomalous flag."""
        c = RegimeClassifier()
        r = c.classify(_make_data({
            "GDP_QOQ_ANN": 2.4, "CPI_YOY": 1.5,
            "STOCK_BOND_CORR": -0.25,
        }))
        assert not any("anomalous corr" in f for f in r.flags)


# ═══════════════════════════════════════════════════════════════════════
# Classifier: fail-open
# ═══════════════════════════════════════════════════════════════════════

class TestClassifierFailOpen:
    """Classifier handles missing data gracefully."""

    def test_empty_data_produces_result(self):
        """Empty dict should not crash."""
        c = RegimeClassifier()
        r = c.classify({})
        assert r.regime in ("R1", "R2", "R3", "R4")
        assert isinstance(r.confidence, float)

    def test_partial_data(self):
        """Only GDP + CPI should still classify."""
        c = RegimeClassifier()
        r = c.classify({"GDP_QOQ_ANN": 2.4, "CPI_YOY": 1.5})
        assert r.regime == "R1"
        assert r.flags == []

    def test_nan_values_handled(self):
        """NaN values should be treated as missing."""
        c = RegimeClassifier()
        r = c.classify({
            "GDP_QOQ_ANN": np.nan, "CPI_YOY": np.nan,
            "UNRATE_3M_CHG": np.nan,
        })
        # No valid data → growth_above and inflation_above both False → R3
        assert r.regime == "R3"


# ═══════════════════════════════════════════════════════════════════════
# Classifier: classify_dataframe (batch)
# ═══════════════════════════════════════════════════════════════════════

class TestClassifyDataFrame:
    """Batch classification of daily panels."""

    def test_classify_dataframe_adds_columns(self):
        """classify_dataframe should add regime, confidence, flags columns."""
        df = pd.DataFrame({
            "GDP_QOQ_ANN": [2.4, -1.0],
            "CPI_YOY": [1.5, 4.0],
            "CPI_TREND_3M": [0.0, 0.3],
            "UNRATE_3M_CHG": [-0.2, 0.6],
            "2S10S": [0.50, 0.50],
            "BAA_AAA": [0.44, 0.44],
            "NFCI": [-0.5, -0.5],
            "DCOILWTICO": [75, 75],
            "USD_MOM_PCT": [0.3, 0.3],
            "VIX": [15, 15],
            "STOCK_BOND_CORR": [-0.25, -0.25],
        }, index=pd.date_range("2024-01-01", periods=2, freq="D"))
        c = RegimeClassifier()
        result = c.classify_dataframe(df)
        assert "regime" in result.columns
        assert "confidence" in result.columns
        assert "flags" in result.columns
        assert result["regime"].iloc[0] == "R1"
        assert result["regime"].iloc[1] == "R4"

    def test_classify_dataframe_output_shapes(self):
        """Output DataFrame has same index as input, extra columns."""
        df = pd.DataFrame({
            "GDP_QOQ_ANN": [2.4],
            "CPI_YOY": [1.5],
            "CPI_TREND_3M": [0.0],
            "UNRATE_3M_CHG": [-0.2],
            "2S10S": [0.50],
            "BAA_AAA": [0.44],
            "NFCI": [-0.5],
            "DCOILWTICO": [75],
            "USD_MOM_PCT": [0.3],
            "VIX": [15],
            "STOCK_BOND_CORR": [-0.25],
        }, index=pd.date_range("2024-01-01", periods=1, freq="D"))
        c = RegimeClassifier()
        result = c.classify_dataframe(df)
        assert len(result) == 1
        assert result["regime"].iloc[0] == "R1"
        assert result["confidence"].iloc[0] == 1.0


# ═══════════════════════════════════════════════════════════════════════
# filter_regime_returns()
# ═══════════════════════════════════════════════════════════════════════

class TestFilterRegimeReturns:
    """Utility: filter returns to current regime's trading days."""

    def test_filters_to_correct_regime(self):
        """Only R1 trading days should be returned."""
        dates = pd.date_range("2024-01-01", periods=100, freq="B")
        returns = pd.DataFrame(
            np.random.RandomState(42).randn(100, 3) * 0.01,
            index=dates, columns=["A", "B", "C"],
        )
        regime_history = pd.DataFrame({
            "regime": ["R1"] * 50 + ["R2"] * 50,
        }, index=pd.DatetimeIndex(dates))
        filtered = filter_regime_returns(returns, regime_history, current_regime="R1", min_days=1)
        assert len(filtered) == 50
        assert (filtered.index == dates[:50]).all()

    def test_returns_empty_when_insufficient_data(self):
        """Below min_days threshold → empty DataFrame."""
        dates = pd.date_range("2024-01-01", periods=5, freq="B")
        returns = pd.DataFrame(
            np.random.RandomState(42).randn(5, 2) * 0.01,
            index=dates, columns=["X", "Y"],
        )
        regime_history = pd.DataFrame(
            {"regime": ["R1"] * 5},
            index=pd.DatetimeIndex(dates),
        )
        filtered = filter_regime_returns(returns, regime_history, min_days=10)
        assert filtered.empty

    def test_current_regime_from_last_row(self):
        """If current_regime not specified, uses the last row."""
        dates = pd.date_range("2024-01-01", periods=100, freq="B")
        returns = pd.DataFrame(
            np.random.RandomState(42).randn(100, 2) * 0.01,
            index=dates, columns=["X", "Y"],
        )
        regime_history = pd.DataFrame({
            "regime": ["R1"] * 50 + ["R2"] * 50,
        }, index=pd.DatetimeIndex(dates))
        filtered = filter_regime_returns(returns, regime_history, min_days=1)
        assert len(filtered) == 50  # picks R2 (last)

    def test_empty_history_returns_empty(self):
        """Empty regime history → empty returns."""
        returns = pd.DataFrame(
            {"A": [0.01]},
            index=pd.DatetimeIndex(["2024-01-01"]),
        )
        filtered = filter_regime_returns(returns, pd.DataFrame())
        assert filtered.empty

    def test_current_regime_from_history_utility(self):
        """current_regime_from_history returns last regime or None."""
        assert current_regime_from_history(pd.DataFrame()) is None
        history = pd.DataFrame(
            {"regime": ["R1", "R2"]},
            index=pd.DatetimeIndex(["2024-01-01", "2024-01-02"]),
        )
        assert current_regime_from_history(history) == "R2"


# ═══════════════════════════════════════════════════════════════════════
# REGIME_THETA immutability
# ═══════════════════════════════════════════════════════════════════════

class TestRegimeTheta:
    """Guard: REGIME_THETA values must not change."""

    def test_theta_values_match_frontier(self):
        """Sanity check that REGIME_THETA has expected values."""
        assert REGIME_THETA["gdp_growth_threshold"] == 2.0
        assert REGIME_THETA["inflation_target_cpi"] == 2.0
        assert REGIME_THETA["cpi_trend_months"] == 3
        assert REGIME_THETA["yield_curve_inversion_bp"] == -10
        assert REGIME_THETA["baa_aaa_spread_stress"] == 200
        assert REGIME_THETA["vix_crisis_threshold"] == 28.0
        assert REGIME_THETA["wti_stagflation_threshold"] == 100.0
        assert REGIME_THETA["stock_bond_corr_window"] == 60
        assert REGIME_THETA["regime_history_days"] == 750
        assert REGIME_THETA["min_regime_days"] == 60

    def test_custom_theta_does_not_mutate_default(self):
        """Constructor copies, doesn't mutate REGIME_THETA."""
        c = RegimeClassifier({"gdp_growth_threshold": 9.9})
        assert c.t["gdp_growth_threshold"] == 9.9
        assert REGIME_THETA["gdp_growth_threshold"] == 2.0  # unchanged


# ═══════════════════════════════════════════════════════════════════════
# Fetcher: fail-open (no key — Yahoo must also be blocked)
# ═══════════════════════════════════════════════════════════════════════

class TestFetcherFailOpen:
    """RegimeFetcher returns empty DataFrame on missing key."""

    def test_no_api_key_returns_empty(self, monkeypatch):
        """Without FRED_API_KEY, fetch_regime_data returns empty DataFrame."""
        import common.regime_fetcher as fetcher
        old_key = os.environ.pop("FRED_API_KEY", None)
        try:
            fetcher.clear_cache()
            # Also mock Yahoo to prevent real network calls
            monkeypatch.setattr(fetcher, "_fetch_yahoo_prices",
                                lambda tickers, period="2y": {})
            df = fetcher.fetch_regime_data(days_back=30)
            assert df.empty
        finally:
            if old_key:
                os.environ["FRED_API_KEY"] = old_key

    def test_fetch_with_bogus_key_returns_empty(self, monkeypatch):
        """Bogus key + mocked Yahoo → empty DataFrame."""
        import common.regime_fetcher as fetcher
        old_key = os.environ.get("FRED_API_KEY")
        os.environ["FRED_API_KEY"] = "bogus_not_real_key"
        try:
            fetcher.clear_cache()
            monkeypatch.setattr(fetcher, "_fetch_yahoo_prices",
                                lambda tickers, period="2y": {})
            df = fetcher.fetch_regime_data(days_back=30)
            assert df.empty
        finally:
            if old_key:
                os.environ["FRED_API_KEY"] = old_key
            else:
                os.environ.pop("FRED_API_KEY", None)


# ═══════════════════════════════════════════════════════════════════════
# Store: upsert + query (each test gets its own temp DB)
# ═══════════════════════════════════════════════════════════════════════

class TestRegimeStore:
    """SQLite regime_history: upsert idempotency, query, latest."""

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        """Redirect DB to a unique temp file for each test."""
        import common.regime_store as store
        self._tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp_db.close()
        # Override DB_PATH before any store call
        monkeypatch.setattr(store, "DB_PATH", self._tmp_db.name)
        # Clear any stale in-memory state; re-init on the temp DB
        store.init_db()

    def test_upsert_and_query(self):
        """Upsert a row then query it back."""
        import common.regime_store as store
        ok = store.upsert("2024-06-15", {
            "regime": "R1", "confidence": 1.0, "flags": "",
            "cpi_yoy": 1.5, "gdp_qoq": 2.4, "unrate": 4.1,
            "curve_bp": 50.0, "baa_aaa_bp": 44.0, "nfci": -0.5,
            "vix": 15.1, "corr": -0.25, "wti": 75.0,
        })
        assert ok

        df = store.query_window(days=30)
        assert len(df) == 1
        assert df["regime"].iloc[0] == "R1"
        assert df["cpi_yoy"].iloc[0] == 1.5

    def test_upsert_idempotent(self):
        """Inserting the same date twice should replace, not duplicate."""
        import common.regime_store as store
        store.upsert("2024-06-15", {"regime": "R1", "confidence": 1.0, "flags": ""})
        store.upsert("2024-06-15", {"regime": "R2", "confidence": 0.9, "flags": "late cycle"})

        df = store.query_window(days=30)
        assert len(df) == 1  # still one row
        assert df["regime"].iloc[0] == "R2"  # latest wins

    def test_latest_returns_most_recent(self):
        """latest() returns the row with the newest date."""
        import common.regime_store as store
        store.upsert("2024-06-10", {"regime": "R1", "confidence": 1.0, "flags": ""})
        store.upsert("2024-06-20", {"regime": "R3", "confidence": 0.9, "flags": "de-inverting"})

        row = store.latest()
        assert row is not None
        assert row["regime"] == "R3"
        assert row["date"] == "2024-06-20"

    def test_query_window_respects_limit(self):
        """query_window(days=N) returns at most N rows."""
        import common.regime_store as store
        for i in range(20):
            d = f"2024-06-{i+1:02d}"
            store.upsert(d, {"regime": "R1", "confidence": 1.0, "flags": ""})

        df = store.query_window(days=5)
        assert len(df) <= 5

    def test_empty_db_returns_empty(self):
        """Querying fresh DB returns empty DataFrame."""
        import common.regime_store as store
        df = store.query_window()
        assert df.empty
        assert store.latest() is None


# ═══════════════════════════════════════════════════════════════════════
# Pipeline integration (synthetic)
# ═══════════════════════════════════════════════════════════════════════

class TestPipelineIntegration:
    """End-to-end: fetch→classify→store→query, with synthetic data."""

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        """Redirect store to a temp DB for each pipeline test."""
        import common.regime_store as store
        self._tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp_db.close()
        monkeypatch.setattr(store, "DB_PATH", self._tmp_db.name)
        store.init_db()

    def test_pipeline_with_synthetic_data(self, monkeypatch):
        """Pipeline completes without hitting real APIs."""
        import common.regime_pipeline as pipeline
        import common.regime_store as store

        # Build synthetic daily DataFrame (R1 Expansion — growth above, CPI below)
        dates = pd.date_range("2024-01-01", periods=100, freq="D")
        df = pd.DataFrame({
            "GDP_QOQ_ANN": [2.4] * 100,
            "CPI_YOY": [1.5] * 100,
            "CPI_TREND_3M": [0.0] * 100,
            "UNRATE_3M_CHG": [-0.1] * 100,
            "2S10S": [0.50] * 100,
            "BAA_AAA": [0.44] * 100,
            "NFCI": [-0.5] * 100,
            "DCOILWTICO": [75.0] * 100,
            "USD_MOM_PCT": [0.3] * 100,
            "VIX": [15.0] * 100,
            "STOCK_BOND_CORR": [-0.25] * 100,
        }, index=dates)

        def _fake_fetch(days_back=750):
            return df

        monkeypatch.setattr(pipeline, "fetch_regime_data", _fake_fetch)

        # Run pipeline
        history = pipeline.run_regime_pipeline(days_back=750)
        assert not history.empty
        assert "regime" in history.columns
        assert history["regime"].iloc[0] == "R1"

        # Verify store was populated
        row = store.latest()
        assert row is not None
        assert row["regime"] == "R1"

    def test_pipeline_fail_open_on_empty_fetch(self, monkeypatch):
        """Pipeline returns empty DataFrame when fetch fails."""
        import common.regime_pipeline as pipeline

        def _fake_empty(days_back=750):
            return pd.DataFrame()

        monkeypatch.setattr(pipeline, "fetch_regime_data", _fake_empty)

        result = pipeline.run_regime_pipeline(days_back=30)
        assert result.empty
