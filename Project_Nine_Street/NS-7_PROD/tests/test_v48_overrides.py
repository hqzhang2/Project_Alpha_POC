#!/usr/bin/env python3
"""Test v4.8 — D1 PM-intent overrides persistence.

Covers research_d1_overrides_v48.md:
  - load_overrides fail-open (missing/corrupt → {})
  - save_overrides full-replace + normalized keep list
  - build_basket precedence: explicit args > overrides > config defaults
"""
import json
from pathlib import Path

import pytest

import config
import d1_basket


@pytest.fixture(autouse=True)
def _overrides_path(tmp_path, monkeypatch):
    """Redirect the overrides file into a tmp dir for every test."""
    p = tmp_path / "d1_overrides.json"
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    # d1_basket reads via Path(config.DATA_DIR) / "d1_overrides.json"
    yield p


# ── load_overrides ────────────────────────────────────────────────────────
def test_load_overrides_missing_fail_open(_overrides_path):
    assert d1_basket.load_overrides() == {}


def test_load_overrides_corrupt_fail_open(_overrides_path):
    _overrides_path.write_text("{not json")
    assert d1_basket.load_overrides() == {}


def test_load_overrides_non_dict_fail_open(_overrides_path):
    _overrides_path.write_text("[1,2]")
    assert d1_basket.load_overrides() == {}


# ── save_overrides ────────────────────────────────────────────────────────
def test_save_overrides_full_replace_and_normalizes_keep(_overrides_path):
    d1_basket.save_overrides("risk_normalized", 20, ["hood", "hum", "HOOD"])
    doc = json.loads(_overrides_path.read_text())
    assert doc["method"] == "risk_normalized"
    assert doc["top_n"] == 20
    assert doc["keep"] == ["HOOD", "HUM"]          # deduped + uppercased
    assert doc["source"] == "dashboard_apply"
    # full replace: second save overwrites, no merge
    d1_basket.save_overrides("rank_tilted", 10, None)
    doc2 = json.loads(_overrides_path.read_text())
    assert doc2["method"] == "rank_tilted"
    assert doc2["keep"] is None


def test_save_overrides_none_keep_means_all_selected(_overrides_path):
    d1_basket.save_overrides("momentum_score", None, None)
    doc = json.loads(_overrides_path.read_text())
    assert doc["keep"] is None
    assert doc["method"] == "momentum_score"


# ── build_basket precedence ───────────────────────────────────────────────
def _fake_selection(monkeypatch, n=30):
    scores = [{"ticker": f"T{i:02d}", "score": 100 - i,
               "rank": i + 1} for i in range(n)]
    sel = {"as_of": "2026-08-23", "scores": scores,
           "benchmarks": {"spy": 0.1}}
    monkeypatch.setattr(d1_basket, "load_selection", lambda: sel)
    return sel


def test_build_basket_prefers_explicit_args_over_overrides(
        monkeypatch, _overrides_path):
    _fake_selection(monkeypatch)
    _overrides_path.write_text(json.dumps(
        {"method": "rank_tilted", "top_n": 5}))
    doc = d1_basket.build_basket(method="momentum_score", n=7)
    assert doc["method"] == "momentum_score"       # explicit wins
    assert doc["top_n"] == 7


def test_build_basket_uses_overrides_when_no_args(monkeypatch, _overrides_path):
    _fake_selection(monkeypatch)
    _overrides_path.write_text(json.dumps(
        {"method": "risk_normalized", "top_n": 6}))
    doc = d1_basket.build_basket()
    assert doc["method"] == "risk_normalized"      # overrides beat config default
    assert doc["top_n"] == 6


def test_build_basket_falls_back_to_config_without_overrides(
        monkeypatch, _overrides_path):
    _fake_selection(monkeypatch)
    doc = d1_basket.build_basket()                 # no file at all
    assert doc["method"] == config.D1_WEIGHT_METHOD
