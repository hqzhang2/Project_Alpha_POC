"""
Alpha Terminal Regime API — tests (synthetic + offline only).

Tests for:
  - get_regime(): latest classification payload shape + values
  - get_regime_history(): {date, regime, confidence, flags} rows
  - Fail-open: empty store → {'regime': 'N/A', ...} and {'history': []}

ALL tests monkeypatch common.regime_store.query_window — no network, no DB.

Run: pytest tests/test_regime_api.py -q (inside QA_terminal/)
"""
from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import regime as regime_mod


def _make_history_df():
    """Synthetic store rows (matching common.regime_store.query_window shape)."""
    idx = pd.date_range("2026-07-01", periods=5, freq="B")
    return pd.DataFrame({
        "regime": ["R1", "R1", "R2", "R2", "R2"],
        "confidence": [1.0, 1.0, 0.9, 0.9, 0.9],
        "flags": ["", "", "credit stress", "", ""],
        "cpi_yoy": [1.8, 1.9, 2.5, 3.0, 3.2],
        "gdp_qoq": [2.4, 2.4, 1.9, 1.5, 1.5],
        "unrate": [4.0, 4.1, 4.2, 4.2, 4.1],
        "curve_bp": [50.0, 48.0, 45.0, 44.0, 44.0],
        "baa_aaa_bp": [40.0, 42.0, 44.0, 44.0, 44.0],
        "nfci": [-0.4, -0.3, -0.5, -0.5, -0.5],
        "vix": [15.0, 14.5, 15.8, 15.1, 14.9],
        "corr": [-0.2, -0.22, 0.3, 0.41, 0.41],
        "wti": [70.0, 72.0, 80.0, 81.9, 81.9],
    }, index=idx)


class TestGetRegime:
    def test_latest_payload(self, monkeypatch):
        """Latest row → full payload with correct values."""
        monkeypatch.setattr(regime_mod, "_read_history", lambda days=730: _make_history_df())
        payload = regime_mod.get_regime()
        assert payload["regime"] == "R2"
        assert payload["confidence"] == 0.9
        assert payload["cpi_yoy"] == 3.2
        assert payload["gdp_qoq"] == 1.5
        assert payload["unrate"] == 4.1
        assert payload["curve_bp"] == 44.0
        assert payload["corr"] == 0.41
        assert payload["as_of"] == "2026-07-07"  # last business day

    def test_fail_open_empty_store(self, monkeypatch):
        monkeypatch.setattr(regime_mod, "_read_history", lambda days=730: pd.DataFrame())
        payload = regime_mod.get_regime()
        assert payload["regime"] == "N/A"
        assert "error" in payload

    def test_fail_open_raises(self, monkeypatch):
        """query_window throwing → N/A (never crash)."""
        import common.regime_store as store_mod

        def _boom(days=750):
            raise RuntimeError("store corrupt")
        monkeypatch.setattr(store_mod, "query_window", _boom)
        payload = regime_mod.get_regime()
        assert payload["regime"] == "N/A"


class TestGetRegimeHistory:
    def test_history_rows(self, monkeypatch):
        """History → list of {date, regime, confidence, flags} oldest→newest."""
        monkeypatch.setattr(regime_mod, "_read_history", lambda days=730: _make_history_df())
        result = regime_mod.get_regime_history(days=730)
        rows = result["history"]
        assert len(rows) == 5
        assert rows[0]["date"] == "2026-07-01"
        assert rows[0]["regime"] == "R1"
        assert rows[-1]["regime"] == "R2"
        assert rows[2]["flags"] == "credit stress"
        # oldest → newest ordering
        assert rows[0]["date"] < rows[-1]["date"]

    def test_fail_open_empty(self, monkeypatch):
        monkeypatch.setattr(regime_mod, "_read_history", lambda days=730: pd.DataFrame())
        assert regime_mod.get_regime_history(days=730) == {"history": []}

    def test_history_days_param(self, monkeypatch):
        """days param is forwarded to _read_history."""
        captured = {}
        def _fake(days=730):
            captured["days"] = days
            return _make_history_df()
        monkeypatch.setattr(regime_mod, "_read_history", _fake)
        regime_mod.get_regime_history(days=100)
        assert captured["days"] == 100
