#!/usr/bin/env python3
"""Test feed_sources — NS-5 v4.5 source feed loader (DPF-owned tests).

Covers: single-source load (D1/NS8/NSETF), ALL merge (overlap sums, fail-open),
staleness fail-open, unknown-source error, weight normalization, availability.
These mirror the construction logic in feed_sources.py — do not edit the
signal semantics without DPF.
"""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

import config
import feed_sources as fs


def _fresh_doc():
    """A valid, fresh signals.json-shaped doc (as_of = today)."""
    return {
        "as_of": date.today().isoformat(),
        "weights": {"SPY": 0.5, "TLT": 0.3, "DBC": 0.2},
    }


def _write_feed(path: Path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc))


def _stale_doc():
    return {
        "as_of": (date.today() - timedelta(days=10)).isoformat(),
        "weights": {"SPY": 1.0},
    }


# ── weight extraction / normalization ────────────────────────────────────
def test_weights_from_normalizes_and_drops_nonpositive():
    doc = {"weights": {"A": 0.5, "B": 0.5, "C": 0.0, "D": -1.0}}
    out = fs._weights_from(doc)
    assert out == {"A": 0.5, "B": 0.5}
    assert abs(sum(out.values()) - 1.0) < 1e-12


def test_weights_from_empty_fail_open():
    assert fs._weights_from({}) == {}
    assert fs._weights_from({"weights": {}}) == {}
    assert fs._weights_from({"weights": {"A": 0.0}}) == {}


# ── staleness ────────────────────────────────────────────────────────────
def test_is_stale_true_on_missing_as_of():
    assert fs._is_stale({"weights": {"A": 1.0}}) is True


def test_is_stale_threshold(tmp_path):
    fresh = _fresh_doc()
    stale = _stale_doc()
    assert fs._is_stale(fresh) is False
    assert fs._is_stale(stale) is True


# ── single source load ───────────────────────────────────────────────────
def test_load_single_d1(tmp_path, monkeypatch):
    d = _fresh_doc()
    _write_feed(tmp_path / "d1.json", d)
    monkeypatch.setattr(config, "D1_BASKET_PATH", tmp_path / "d1.json")
    out = fs.load_source("D1")
    assert abs(sum(out.values()) - 1.0) < 1e-12
    assert out["SPY"] == pytest.approx(0.5)


def test_load_single_ns8(tmp_path, monkeypatch):
    d = _fresh_doc()
    _write_feed(tmp_path / "ns8.json", d)
    monkeypatch.setattr(config, "NS8_SIGNALS_PATH", tmp_path / "ns8.json")
    out = fs.load_source("NS8")
    assert out["TLT"] == pytest.approx(0.3)


def test_load_single_nsetf(tmp_path, monkeypatch):
    d = _fresh_doc()
    _write_feed(tmp_path / "nsetf.json", d)
    monkeypatch.setattr(config, "NSETF_SIGNALS_PATH", tmp_path / "nsetf.json")
    out = fs.load_source("NSETF")
    assert out["DBC"] == pytest.approx(0.2)


def test_load_single_missing_fail_open(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "D1_BASKET_PATH", tmp_path / "nope.json")
    assert fs.load_source("D1") == {}


def test_load_single_stale_fail_open(tmp_path, monkeypatch):
    _write_feed(tmp_path / "ns8.json", _stale_doc())
    monkeypatch.setattr(config, "NS8_SIGNALS_PATH", tmp_path / "ns8.json")
    assert fs.load_source("NS8") == {}


def test_load_single_case_insensitive(tmp_path, monkeypatch):
    d = _fresh_doc()
    _write_feed(tmp_path / "nsetf.json", d)
    monkeypatch.setattr(config, "NSETF_SIGNALS_PATH", tmp_path / "nsetf.json")
    assert fs.load_source("nsetf") != {}


def test_load_unknown_source_raises():
    with pytest.raises(ValueError):
        fs.load_source("BOGUS")


# ── ALL merge ────────────────────────────────────────────────────────────
def test_load_all_merges_overlap_sums(tmp_path, monkeypatch):
    a = {"as_of": date.today().isoformat(), "weights": {"X": 0.5, "Y": 0.5}}
    b = {"as_of": date.today().isoformat(), "weights": {"Y": 0.5, "Z": 0.5}}
    _write_feed(tmp_path / "d1.json", a)
    _write_feed(tmp_path / "ns8.json", b)
    monkeypatch.setattr(config, "D1_BASKET_PATH", tmp_path / "d1.json")
    monkeypatch.setattr(config, "NS8_SIGNALS_PATH", tmp_path / "ns8.json")
    monkeypatch.setattr(config, "NSETF_SIGNALS_PATH", tmp_path / "nope.json")
    out = fs.load_source("ALL")
    # X:0.5, Y:0.5+0.5=1.0, Z:0.5 → normalized
    assert set(out) == {"X", "Y", "Z"}
    # Y (overlap) should be the largest; all weights positive, sum to 1
    assert out["Y"] > out["X"] and out["Y"] > out["Z"]
    assert abs(sum(out.values()) - 1.0) < 1e-12


def test_load_all_surviving_sleeve_carries(tmp_path, monkeypatch):
    # Only NSETF is fresh; D1 and NS8 are stale/missing → ALL = NSETF alone
    nsetf = {"as_of": date.today().isoformat(), "weights": {"GLD": 1.0}}
    _write_feed(tmp_path / "d1.json", _stale_doc())
    _write_feed(tmp_path / "ns8.json", _stale_doc())
    _write_feed(tmp_path / "nsetf.json", nsetf)
    monkeypatch.setattr(config, "D1_BASKET_PATH", tmp_path / "d1.json")
    monkeypatch.setattr(config, "NS8_SIGNALS_PATH", tmp_path / "ns8.json")
    monkeypatch.setattr(config, "NSETF_SIGNALS_PATH", tmp_path / "nsetf.json")
    out = fs.load_source("ALL")
    assert out == {"GLD": 1.0}


def test_load_all_all_stale_fail_open(tmp_path, monkeypatch):
    _write_feed(tmp_path / "d1.json", _stale_doc())
    _write_feed(tmp_path / "ns8.json", _stale_doc())
    _write_feed(tmp_path / "nsetf.json", _stale_doc())
    monkeypatch.setattr(config, "D1_BASKET_PATH", tmp_path / "d1.json")
    monkeypatch.setattr(config, "NS8_SIGNALS_PATH", tmp_path / "ns8.json")
    monkeypatch.setattr(config, "NSETF_SIGNALS_PATH", tmp_path / "nsetf.json")
    assert fs.load_source("ALL") == {}


# ── availability ─────────────────────────────────────────────────────────
def test_source_availability(tmp_path, monkeypatch):
    _write_feed(tmp_path / "d1.json", _fresh_doc())
    _write_feed(tmp_path / "ns8.json", _stale_doc())
    monkeypatch.setattr(config, "D1_BASKET_PATH", tmp_path / "d1.json")
    monkeypatch.setattr(config, "NS8_SIGNALS_PATH", tmp_path / "ns8.json")
    monkeypatch.setattr(config, "NSETF_SIGNALS_PATH", tmp_path / "nope.json")
    assert fs.source_availability() == {"D1": True, "NS8": False, "NSETF": False}
