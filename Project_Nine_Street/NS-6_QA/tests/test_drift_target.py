#!/usr/bin/env python3
"""NS-6 drift TARGET resolution tests (PM decision 2026-08-13, option 2).

The drift target = the SELECTED portfolio's policy (config.PORTFOLIO_POLICIES
→ NS-5 policies.json), falling back to DEFAULT_WEIGHTS. Hermetic — the NS-5
policy store is monkeypatched to a temp file; no live stores touched.

Run: env -u PYTHONPATH python3 -m pytest tests/test_drift_target.py -q
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import qa_server
import store


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Isolated NS-6 store (temp db) + NS-5 policy path (temp file)."""
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "ns6.db")
    store.init_db()
    monkeypatch.setattr(qa_server, "NS5_POLICIES_PATH", tmp_path / "policies.json")
    monkeypatch.setattr(qa_server, "DEFAULT_WEIGHTS",
                        {"SPY": 0.6, "TLT": 0.4})
    return tmp_path


def _handler():
    """A real NS6Handler instance without __init__ (methods under test
    don't touch HTTP state)."""
    return object.__new__(qa_server.NS6Handler)


def test_mapped_portfolio_uses_its_policy(tmp_path, monkeypatch):
    (tmp_path / "policies.json").write_text(json.dumps({
        "60/40 SPY/TLT": {"SPY": 0.6, "TLT": 0.4},
        "70/30 SPY/TLT": "{\"SPY\": 0.7, \"TLT\": 0.3}"}))  # stringified value
    monkeypatch.setattr(store, "get_portfolio_source", lambda: "Hyperscaler")
    monkeypatch.setattr(config, "PORTFOLIO_POLICIES", {"Hyperscaler": "60/40 SPY/TLT"})
    weights, label = _handler()._drift_target()
    assert label == "policy:60/40 SPY/TLT"
    assert weights == {"SPY": 0.6, "TLT": 0.4}
    # The stringified policy parses too (real NS-5 shape).
    assert _handler()._ns5_policies()["70/30 SPY/TLT"] == {"SPY": 0.7, "TLT": 0.3}


def test_unmapped_portfolio_falls_back_to_default(tmp_path, monkeypatch):
    (tmp_path / "policies.json").write_text(json.dumps({"60/40 SPY/TLT": {"SPY": 0.6, "TLT": 0.4}}))
    monkeypatch.setattr(store, "get_portfolio_source", lambda: "OtherBook")
    monkeypatch.setattr(config, "PORTFOLIO_POLICIES", {"Hyperscaler": "60/40 SPY/TLT"})
    weights, label = _handler()._drift_target()
    assert label == "default"
    assert weights == qa_server.DEFAULT_WEIGHTS


def test_policy_name_missing_from_store_falls_back(tmp_path, monkeypatch):
    (tmp_path / "policies.json").write_text(json.dumps({}))
    monkeypatch.setattr(store, "get_portfolio_source", lambda: "Hyperscaler")
    monkeypatch.setattr(config, "PORTFOLIO_POLICIES", {"Hyperscaler": "60/40 SPY/TLT"})
    weights, label = _handler()._drift_target()
    assert label == "default"
    assert weights == qa_server.DEFAULT_WEIGHTS


def test_model_source_uses_default(tmp_path, monkeypatch):
    (tmp_path / "policies.json").write_text(json.dumps({"60/40 SPY/TLT": {"SPY": 0.6, "TLT": 0.4}}))
    monkeypatch.setattr(store, "get_portfolio_source", lambda: "model")
    weights, label = _handler()._drift_target()
    assert label == "default"
    assert weights == qa_server.DEFAULT_WEIGHTS


def test_ns5_policies_fail_open(tmp_path, monkeypatch):
    monkeypatch.setattr(qa_server, "NS5_POLICIES_PATH", tmp_path / "missing.json")
    assert _handler()._ns5_policies() == {}
