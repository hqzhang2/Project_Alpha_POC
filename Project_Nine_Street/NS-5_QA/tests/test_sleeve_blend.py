#!/usr/bin/env python3
"""NS-5 Sleeve Blend module tests (2b — joint universe, DESIGN §4.3).

Hermetic — no network, no live stores: the sleeve reads and the regime are
monkeypatched; only the pure construction + assembly logic is exercised.

Run with clean env:
  env -u PYTHONPATH python3 -m pytest tests/test_sleeve_blend.py -q
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import sleeve_blend as sb


# ── Pure construction ────────────────────────────────────────────────────
def test_build_blend_weights():
    growth = ["AAA", "BBB", "CCC"]        # 3 momentum names
    value = ["VVV", "WWW"]                # 2 value names
    doc = sb.build_blend(growth, value, (0.80, 0.20))
    b = doc["blended"]
    assert b["AAA"] == pytest.approx(0.80 / 3)
    assert b["VVV"] == pytest.approx(0.20 / 2)
    assert doc["guardrails"]["n"] == 5
    assert doc["guardrails"]["weights_sum"] == pytest.approx(1.0, abs=1e-9)
    # max_weight is rounded to 4dp for display.
    assert doc["guardrails"]["max_weight"] == pytest.approx(0.80 / 3, abs=1e-3)


def test_build_blend_eff_n():
    # Equal-weight 20-name book → effN 20.
    doc = sb.build_blend(list(map(str, range(20))), [], (1.0, 0.0))
    assert doc["guardrails"]["eff_n"] == pytest.approx(20.0, abs=0.01)
    # Defensive tilt 50/50 over 20+20 → effN 40.
    doc2 = sb.build_blend(list(map(str, range(20))), list(map(str, range(20, 40))),
                         (0.50, 0.50))
    assert doc2["guardrails"]["eff_n"] == pytest.approx(40.0, abs=0.01)


def test_build_blend_overlap_sums():
    # A name in BOTH sleeves keeps both allocations (union, not clobber).
    doc = sb.build_blend(["AAA", "BBB"], ["AAA", "VVV"], (0.80, 0.20))
    b = doc["blended"]
    assert b["AAA"] == pytest.approx(0.80 / 2 + 0.20 / 2)   # 0.4 + 0.1
    assert b["BBB"] == pytest.approx(0.40)
    assert b["VVV"] == pytest.approx(0.10)
    assert doc["guardrails"]["n"] == 3
    assert doc["guardrails"]["weights_sum"] == pytest.approx(1.0, abs=1e-9)


def test_build_blend_empty_sleeves():
    doc = sb.build_blend([], [], (0.8, 0.2))
    assert doc["blended"] == {}
    assert doc["guardrails"]["n"] == 0
    assert doc["guardrails"]["eff_n"] == 0.0
    assert doc["guardrails"]["max_weight"] == 0.0


def test_build_blend_fail_open_single_sleeve():
    # Value sleeve unavailable → momentum carries 100% of the book.
    doc = sb.build_blend(["AAA", "BBB"], [], (0.8, 0.2))
    assert set(doc["blended"]) == {"AAA", "BBB"}
    assert sum(doc["blended"].values()) == pytest.approx(1.0)


# ── Sleeve reads (fail-open) ─────────────────────────────────────────────
def test_growth_sleeve_reads_selection_json(tmp_path):
    p = tmp_path / "selection.json"
    p.write_text(json.dumps({"selections": [
        {"ticker": "DELL", "rank": 1}, {"ticker": "MRVL", "rank": 2}]}))
    assert sb.growth_sleeve(str(p)) == ["DELL", "MRVL"]


def test_growth_sleeve_missing_file_fail_open():
    assert sb.growth_sleeve("/nonexistent/selection.json") == []


def test_value_sleeve_parses_agreement(monkeypatch):
    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"results": [
                {"ticker": "V1", "agreement": 2},   # in
                {"ticker": "V2", "agreement": 4},   # in
                {"ticker": "V3", "agreement": 1},   # out (< 2)
            ]}).encode()

    def fake_urlopen(u, timeout):
        assert "/api/fundamentals/screen" in u
        return FakeResp()

    monkeypatch.setattr(sb.urllib.request, "urlopen", fake_urlopen)
    val = sb.value_sleeve()
    assert val == ["V1", "V2"]   # agreement ≥ 2, screener order preserved


def test_value_sleeve_network_down_fail_open(monkeypatch):
    def boom(u, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(sb.urllib.request, "urlopen", boom)
    assert sb.value_sleeve() == []


def test_regime_class_mapping(monkeypatch):
    calls = []

    def fake_latest():
        calls.append(1)
        return {"date": "2026-08-13", "regime": reg_value}

    import common.regime_store as rs
    monkeypatch.setattr(rs, "latest", fake_latest)
    reg_value = "R2"
    assert sb.regime_class() == "growth"
    reg_value = "R3"
    assert sb.regime_class() == "defensive"


# ── Assembly (main, monkeypatched reads) ─────────────────────────────────
def test_main_writes_blend_doc(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BLEND_PATH", tmp_path / "sleeve_blend.json")
    monkeypatch.setattr(sb, "growth_sleeve", lambda: ["DELL", "MRVL"])
    monkeypatch.setattr(sb, "value_sleeve", lambda: ["V1", "V2"])
    monkeypatch.setattr(sb, "regime_class", lambda: "growth")
    assert sb.main() == 0
    doc = json.loads((tmp_path / "sleeve_blend.json").read_text())
    assert doc["regime"] == "growth"
    assert doc["sleeve_weights"] == {"momentum": 0.8, "value": 0.2}
    assert doc["growth_sleeve"] == ["DELL", "MRVL"]
    assert doc["blended"]["DELL"] == pytest.approx(0.4)
    assert doc["blended"]["V1"] == pytest.approx(0.1)
    assert doc["guardrails"]["weights_sum"] == pytest.approx(1.0)
