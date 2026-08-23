#!/usr/bin/env python3
"""Tests for d1_basket — DeltaOne portfolio construction (DPF-owned).

Covers: top-n selection (fixed editable n, thin pool), all four weighting
methods, guardrail cap-and-redistribute, contract shape of build_basket,
fail-open on missing selection. Hermetic: no DB, no network.
Run: python3 -m pytest tests/test_d1_basket.py -q   (from NS-7_QA/)
"""
import json
from pathlib import Path

import pytest

import config
import d1_basket as d1


def _cands(n=20):
    """n candidates with descending momentum and rank 1..n."""
    return [{"ticker": f"T{i}", "momentum": 2.0 - 0.1 * i, "rank": i + 1}
            for i in range(n)]


@pytest.fixture
def _cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SELECTION_PATH", tmp_path / "selection.json")
    monkeypatch.setattr(config, "D1_BASKET_PATH", tmp_path / "d1_basket.json")
    return tmp_path


# ── selection / candidates ───────────────────────────────────────────────
def test_load_selection_missing_fail_open(_cfg):
    assert d1.load_selection() is None
    assert d1.build_basket() is None          # no basket → no D1 sleeve


def test_top_candidates_fixed_n():
    c = d1.top_candidates(_cands(30), n=10)
    assert [x["ticker"] for x in c] == [f"T{i}" for i in range(10)]
    assert len(d1.top_candidates(_cands(5), n=20)) == 5     # thin pool → thin basket


# ── momentum_score (default) ─────────────────────────────────────────────
def test_momentum_score_orders_and_floors():
    w = d1.weight_basket(_cands(5), "momentum_score")
    assert w["T0"] > w["T1"] > w["T4"]
    neg = [{"ticker": "N", "momentum": -1.0, "rank": 1}]
    assert d1.weight_basket(neg, "momentum_score") == {"N": 1.0}  # floor+fallback


def test_all_negative_pool_falls_back_equal():
    cands = [{"ticker": t, "momentum": -1.0, "rank": i + 1}
             for i, t in enumerate(["A", "B", "C"])]
    w = d1.weight_basket(cands, "momentum_score")
    assert sum(w.values()) == 3 and len(set(w.values())) == 1


# ── rank_tilted ──────────────────────────────────────────────────────────
def test_rank_tilted_linear():
    w = d1.weight_basket([{"ticker": "A", "rank": 1},
                          {"ticker": "B", "rank": 2},
                          {"ticker": "C", "rank": 3}], "rank_tilted")
    assert w["A"] > w["B"] > w["C"]


def test_rank_tilted_geometric(monkeypatch):
    monkeypatch.setattr(config, "D1_RANK_TILT_GEOMETRIC", True)
    w = d1.weight_basket([{"ticker": "A", "rank": 1},
                          {"ticker": "B", "rank": 2}], "rank_tilted")
    assert w["A"] / w["B"] == pytest.approx(2.0)


# ── risk_normalized ──────────────────────────────────────────────────────
def test_risk_normalized_inverse_vol():
    closes = {"LOW": list(range(100, 160)),           # steady → low vol
              "JAG": [100, 130, 90, 140, 80] * 12}    # wild → high vol
    w = d1.weight_basket(
        [{"ticker": "LOW"}, {"ticker": "JAG"}], "risk_normalized",
        closes_by_ticker=closes)
    assert w["LOW"] > w["JAG"]


def test_risk_normalized_no_data_falls_back_equal():
    w = d1.weight_basket([{"ticker": "X"}, {"ticker": "Y"}],
                         "risk_normalized", closes_by_ticker={})
    assert w["X"] == w["Y"] == 1.0


# ── tenure_aware ─────────────────────────────────────────────────────────
def test_tenure_aware_decays_long_tooth():
    cands = _cands(4)
    base = d1.weight_basket(cands, "momentum_score")
    # same momentum shape; T0 fresh, T3 long-tooth
    ten = {"T0": 5, "T1": 5, "T2": 300, "T3": 400}
    w = d1.weight_basket(cands, "tenure_aware", tenure=ten)
    # fresh names keep full relative weight; long-tooth decayed by min factor
    assert w["T1"] == pytest.approx(base["T1"])
    assert w["T2"] == pytest.approx(base["T2"] * config.D1_TENURE_MIN_FACTOR)
    assert w["T3"] == pytest.approx(base["T3"] * config.D1_TENURE_MIN_FACTOR)


def test_tenure_aware_unknown_tenure_neutral():
    cands = _cands(2)
    base = d1.weight_basket(cands, "momentum_score")
    w = d1.weight_basket(cands, "tenure_aware", tenure={"T0": None, "T1": None})
    assert w == base


def test_unknown_method_raises():
    with pytest.raises(ValueError):
        d1.weight_basket(_cands(3), "bogus")


# ── guardrails ───────────────────────────────────────────────────────────
def test_guardrails_cap_and_redistribute():
    w = {"BIG": 0.9, "s1": 0.05, "s2": 0.05}
    out = d1.apply_guardrails(w, max_w=0.40)
    assert out["BIG"] <= 0.40 + 1e-9
    assert abs(sum(out.values()) - 1.0) < 1e-9
    assert out["BIG"] < 0.9            # redistributed, not re-inflated


def test_guardrails_all_at_cap_leaves_valid():
    out = d1.apply_guardrails({"A": 0.5, "B": 0.5}, max_w=0.08)
    assert abs(sum(out.values()) - 1.0) < 1e-9


def test_effective_n():
    eq = {t: 1 / 20 for t in [f"T{i}" for i in range(20)]}
    assert d1.effective_n(eq) == pytest.approx(20.0)
    assert d1.effective_n({}) == 0.0


# ── build_basket contract ────────────────────────────────────────────────
def test_build_basket_contract_shape(_cfg):
    sel = {"as_of": "2026-08-22", "scores": _cands(25),
           "benchmarks": {"spy": 0.1, "qqq": 0.17}}
    (_cfg / "selection.json").write_text(json.dumps(sel))
    doc = d1.build_basket()
    for key in ("as_of", "service", "strategy", "method", "selection_as_of",
                "top_n", "guardrails", "weights", "eff_n", "max_weight"):
        assert key in doc
    assert doc["strategy"] == "deltaone"
    assert doc["top_n"] == config.BASKET_TOP_N
    assert abs(sum(doc["weights"].values()) - 1.0) < 1e-6
    assert all(w <= config.D1_MAX_NAME_W + 1e-6 for w in doc["weights"].values())
    assert doc["selection_as_of"] == "2026-08-22"


def test_build_basket_thin_pool(_cfg):
    sel = {"as_of": "2026-08-22", "scores": _cands(8)}
    (_cfg / "selection.json").write_text(json.dumps(sel))
    doc = d1.build_basket()
    assert doc["top_n"] == 8               # pool thinner than n → take what exists


def test_main_writes_file(_cfg, capsys):
    sel = {"as_of": "2026-08-22", "scores": _cands(25),
           "benchmarks": {"spy": 0.1}}
    (_cfg / "selection.json").write_text(json.dumps(sel))
    assert d1.main() == 0
    out = json.loads((config.D1_BASKET_PATH).read_text())
    assert out["weights"]
