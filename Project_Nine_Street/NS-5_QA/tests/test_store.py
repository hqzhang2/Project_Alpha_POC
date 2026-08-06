#!/usr/bin/env python3
"""
NS-5 Portfolio Store + shares→weights tests.

Run with clean env:
  env -i HOME=$HOME /usr/bin/python3 -m pytest tests/test_store.py -q
No network — uses tmp_path for store files, synthetic closes.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import portfolio_store
from portfolio import shares_to_weights


@pytest.fixture()
def store(monkeypatch, tmp_path):
    """Point the store at a temp dir so tests never touch real data/."""
    tmp = tmp_path / "data"
    tmp.mkdir()
    monkeypatch.setattr(portfolio_store, "PORTFOLIOS_PATH", tmp / "portfolios.json")
    monkeypatch.setattr(portfolio_store, "POLICIES_PATH", tmp / "policies.json")
    return portfolio_store


class TestPortfolioCRUD:
    def test_upsert_get_list(self, store):
        store.upsert_portfolio("A", {"AAPL": 10, "TLT": 100})
        store.upsert_portfolio("B", {"SPY": 5})
        assert store.list_portfolios() == ["A", "B"]
        assert store.get_portfolio("A") == {"AAPL": 10.0, "TLT": 100.0}
        assert store.get_portfolio("ZZZ") is None

    def test_upsert_overwrites(self, store):
        store.upsert_portfolio("A", {"AAPL": 10})
        store.upsert_portfolio("A", {"AAPL": 20, "MSFT": 30})
        assert store.get_portfolio("A") == {"AAPL": 20.0, "MSFT": 30.0}

    def test_delete(self, store):
        store.upsert_portfolio("A", {"AAPL": 1})
        assert store.delete_portfolio("A") is True
        assert store.delete_portfolio("A") is False
        assert store.list_portfolios() == []

    def test_rename(self, store):
        store.upsert_portfolio("Old", {"AAPL": 1})
        out = store.rename_portfolio("Old", "New")
        assert out["name"] == "New"
        assert store.get_portfolio("Old") is None
        assert store.get_portfolio("New") == {"AAPL": 1.0}

    def test_name_required(self, store):
        with pytest.raises(ValueError):
            store.upsert_portfolio("  ", {"AAPL": 1})

    def test_negative_shares_rejected(self, store):
        with pytest.raises(ValueError):
            store.upsert_portfolio("A", {"AAPL": -5})

    def test_empty_holdings_rejected(self, store):
        with pytest.raises(ValueError):
            store.upsert_portfolio("A", {})

    def test_ticker_normalized_upper(self, store):
        store.upsert_portfolio("A", {"aapl": 10})
        assert store.get_portfolio("A") == {"AAPL": 10.0}


class TestPolicyCRUD:
    def test_upsert_get(self, store):
        store.upsert_policy("P1", {"SPY": 0.6, "TLT": 0.4})
        assert store.get_policy("P1") == {"SPY": 0.6, "TLT": 0.4}
        assert store.list_policies() == ["P1"]

    def test_delete_policy(self, store):
        store.upsert_policy("P1", {"SPY": 1.0})
        assert store.delete_policy("P1") is True
        assert store.list_policies() == []

    def test_policy_name_required(self, store):
        with pytest.raises(ValueError):
            store.upsert_policy("", {"SPY": 1.0})


class TestSharesToWeights:
    def _closes(self):
        dates = pd.bdate_range("2024-01-01", periods=5)
        return pd.DataFrame({
            "AAPL": [100, 101, 102, 103, 104],
            "TLT":  [90, 91, 92, 93, 94],
        }, index=dates)

    def test_basic_conversion(self):
        closes = self._closes()
        w = shares_to_weights({"AAPL": 10, "TLT": 100}, closes)
        # AAPL: 10×104 = 1040; TLT: 100×94 = 9400; total 10440
        assert w["AAPL"] == pytest.approx(1040 / 10440)
        assert w["TLT"] == pytest.approx(9400 / 10440)

    def test_missing_ticker_dropped(self):
        closes = self._closes()
        w = shares_to_weights({"AAPL": 10, "ZZZZ": 50}, closes)
        assert "ZZZZ" not in w
        assert w["AAPL"] == pytest.approx(1.0)  # only remaining

    def test_empty_inputs(self):
        assert shares_to_weights({}, self._closes()) == {}
        assert shares_to_weights({"AAPL": 1}, pd.DataFrame()) == {}

    def test_weights_sum_to_one(self):
        closes = self._closes()
        w = shares_to_weights({"AAPL": 10, "TLT": 100, "SPY": 25}, closes)
        assert sum(w.values()) == pytest.approx(1.0)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
