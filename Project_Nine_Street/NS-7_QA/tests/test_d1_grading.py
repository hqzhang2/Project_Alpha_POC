#!/usr/bin/env python3
"""Tests for d1_grading — D1 basket mark-to-market (DPF-owned).

Covers: weighted daily returns over common dates, partial-book exclusion,
persist via mocked common.db, fail-open on missing basket/prices.
Hermetic: temp sqlite price store, monkeypatched DB writer.
Run: python3 -m pytest tests/test_d1_grading.py -q   (from NS-7_QA/)
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

import config
import d1_grading as dg

_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)   # makes `common.db` importable (hermetic)


@pytest.fixture
def _env(tmp_path, monkeypatch):
    """Temp basket + temp price store; persist captured instead of written."""
    basket = {"strategy": "deltaone", "as_of": "2026-08-10",
              "weights": {"A": 0.6, "B": 0.4}}
    bp = tmp_path / "d1_basket.json"
    bp.write_text(json.dumps(basket))
    monkeypatch.setattr(config, "D1_BASKET_PATH", bp)

    pdb = tmp_path / "prices.db"
    monkeypatch.setattr(config, "D1_MTM_PRICES_DB", pdb)
    conn = sqlite3.connect(str(pdb))
    with conn:
        conn.execute("CREATE TABLE prices (ticker TEXT, date TEXT, close REAL)")
        # A: 100 -> 110 -> 99  (+10%, -10%)
        # B:  50 ->  50 -> 55  (  0%, +10%)
        for t, px in (("A", [100.0, 110.0, 99.0]), ("B", [50.0, 50.0, 55.0])):
            for i, p in enumerate(px):
                conn.execute("INSERT INTO prices VALUES (?,?,?)",
                             (t, f"2026-08-{10+i:02d}", p))
    conn.close()
    return tmp_path, pdb


# ── weighted daily returns (from_as_of=False: full overlap window) ───────
def test_basket_daily_returns_weighted(_env):
    tmp, pdb = _env
    rows = dg.mark_to_market(db_path=pdb, from_as_of=False)
    assert rows is not None and len(rows) == 2
    # day2: 0.6*+10% + 0.4*0% = +6%
    assert rows[0]["return"] == pytest.approx(0.06, abs=1e-8)
    # day3: 0.6*-10% + 0.4*+10% = -2%
    assert rows[1]["return"] == pytest.approx(-0.02, abs=1e-8)
    assert all(r["source"] == "d1_basket_mtm" for r in rows)
    assert rows[0]["date"] == "2026-08-11"      # base day dropped


def test_partial_book_dates_excluded(_env):
    tmp, pdb = _env
    # C has only the first date → union of common dates shrinks to day1 only,
    # so no daily returns are computable if C were weighted; verify A/B unaffected
    conn = sqlite3.connect(str(tmp / "prices.db"))
    with conn:
        conn.execute("INSERT INTO prices VALUES ('C','2026-08-10',7.0)")
    conn.close()
    basket = {"weights": {"A": 0.5, "B": 0.5}}
    (tmp / "d1_basket.json").write_text(json.dumps(basket))
    rows = dg.mark_to_market(db_path=pdb, from_as_of=False)
    assert len(rows) == 2                       # C not in weights → ignored


def test_no_common_dates_fail_open(_env):
    tmp, _ = _env
    (tmp / "d1_basket.json").write_text(
        json.dumps({"weights": {"A": 0.5, "ZZZ": 0.5}}))   # ZZZ has no prices
    assert dg.mark_to_market(db_path=tmp / "prices.db", from_as_of=False) is None


def test_missing_basket_fail_open(tmp_path):
    assert dg.mark_to_market(basket_path=tmp_path / "nope.json") is None


def test_empty_weights_fail_open(tmp_path):
    bp = tmp_path / "d1_basket.json"
    bp.write_text(json.dumps({"weights": {}}))
    assert dg.mark_to_market(basket_path=bp) is None


# ── look-ahead gating (from_as_of=True) ──────────────────────────────────
def test_from_as_of_gates_to_book_lifetime(_env):
    tmp, pdb = _env
    # prices run 08-10..08-12; basket as_of=08-10 → realized = from 08-10 only
    rows = dg.mark_to_market(db_path=pdb, from_as_of=True)
    assert rows is not None and len(rows) == 2
    assert rows[0]["date"] >= "2026-08-10"


def test_from_as_of_no_post_as_of_closes_is_none(_env):
    tmp, pdb = _env
    # basket as_of AFTER all prices → nothing realized yet → None (no look-ahead)
    (tmp / "d1_basket.json").write_text(json.dumps(
        {"as_of": "2026-08-20", "weights": {"A": 0.6, "B": 0.4}}))
    assert dg.mark_to_market(db_path=pdb, from_as_of=True) is None


def test_from_as_of_missing_as_of_refuses(_env):
    tmp, pdb = _env
    (tmp / "d1_basket.json").write_text(json.dumps(
        {"weights": {"A": 0.6, "B": 0.4}}))   # no as_of → cannot gate → refuse
    assert dg.mark_to_market(db_path=pdb, from_as_of=True) is None


# ── persistence ──────────────────────────────────────────────────────────
def test_persist_returns_writes_ns7_strategy(_env, monkeypatch):
    import common.db as db
    captured = {}
    monkeypatch.setattr(db, "write_strategy_returns",
                        lambda sid, rows: captured.update(sid=sid, rows=rows)
                        or True)
    rows = [{"date": "2026-08-11", "return": 0.06, "source": "d1_basket_mtm"}]
    assert dg.persist_returns(rows) is True
    assert captured["sid"] == "ns7"
    assert captured["rows"] == rows


def test_persist_fail_open_when_db_down(monkeypatch):
    import common.db as db
    monkeypatch.setattr(db, "write_strategy_returns", lambda sid, rows: False)
    assert dg.persist_returns([{"date": "x", "return": 0.0}]) is False


def test_main_end_to_end(_env, monkeypatch, capsys):
    import common.db as db
    seen = {}
    monkeypatch.setattr(db, "write_strategy_returns",
                        lambda sid, rows: seen.update(n=len(rows)) or True)
    # _env basket as_of=08-10, prices 08-10..08-12 → main writes 2 realized rows
    assert dg.main() == 0
    assert seen["n"] == 2
