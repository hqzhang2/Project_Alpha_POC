#!/usr/bin/env python3
"""Test v4.7 — ns8_grading MtM (DPF-owned tests, mirror test_d1_grading.py).

Covers: weighted returns incl. SHV, realized-only from as_of (no look-ahead),
fail-open on missing cache/book, persist seam shape.
"""
import json
from pathlib import Path

import pytest

import ns8_grading as g


def _write_cache(tmp_path: Path) -> Path:
    data = {
        "dates": ["2026-08-20", "2026-08-21", "2026-08-22"],
        "tickers": ["SPY", "SHV"],
        "closes": {
            "SPY": [100.0, 110.0, 99.0],
            "SHV": [50.0, 50.0, 50.0],
        },
    }
    p = tmp_path / "hist.json"
    p.write_text(json.dumps(data))
    return p


def _write_book(tmp_path: Path, weights, as_of="2026-08-21") -> Path:
    doc = {"as_of": as_of, "weights": weights}
    p = tmp_path / "signals.json"
    p.write_text(json.dumps(doc))
    return p


def test_book_daily_returns_includes_cash_leg():
    closes = {"SPY": {"2026-08-21": 100.0, "2026-08-22": 110.0},
              "SHV": {"2026-08-21": 50.0, "2026-08-22": 50.5}}
    rows = g.book_daily_returns({"SPY": 0.5, "SHV": 0.5}, closes)
    assert len(rows) == 1
    expected = 0.5 * (110 / 100 - 1) + 0.5 * (50.5 / 50 - 1)
    assert rows[0]["return"] == pytest.approx(expected, abs=1e-6)
    assert rows[0]["source"] == "ns8_book_mtm"


def test_book_daily_returns_skips_zero_prev_close(caplog):
    # v4.9: a zero/missing prev close must NOT raise ZeroDivisionError — the
    # day is dropped (fail-open), never corrupting the stream.
    import logging
    caplog.set_level(logging.WARNING, logger="ns8.ns8_grading")
    closes = {"SPY": {"2026-08-21": 0.0, "2026-08-22": 110.0}}
    rows = g.book_daily_returns({"SPY": 1.0}, closes)
    assert rows == []                       # the 0.0->110 day dropped
    assert "no valid prev close" in caplog.text


def test_book_daily_returns_truncation_is_logged(caplog):
    # SHV shorter than SPY → common window truncated; must log, not crash.
    import logging
    caplog.set_level(logging.INFO, logger="ns8.ns8_grading")
    closes = {"SPY": {"d1": 100, "d2": 110, "d3": 121},
              "SHV": {"d2": 50, "d3": 50.5}}
    rows = g.book_daily_returns({"SPY": 0.5, "SHV": 0.5}, closes)
    assert len(rows) == 1                   # only d2->d3 (common)
    assert "truncated" in caplog.text


def test_mark_to_market_realized_only_from_as_of(tmp_path):
    cache = _write_cache(tmp_path)          # history starts 2026-08-20
    book = _write_book(tmp_path, {"SPY": 1.0}, as_of="2026-08-21")
    rows = g.mark_to_market(basket_path=book, cache_path=cache)
    # realized P&L from as_of FORWARD: the return ON as_of (held that day)
    # and after — the pre-as_of day (2026-08-20→21 base) is dropped
    assert [r["date"] for r in rows] == ["2026-08-21", "2026-08-22"]
    assert rows[0]["return"] == pytest.approx(0.10)


def test_mark_to_market_no_lookahead_without_as_of(tmp_path):
    cache = _write_cache(tmp_path)
    book = _write_book(tmp_path, {"SPY": 1.0}, as_of="")
    rows = g.mark_to_market(basket_path=book, cache_path=cache)
    assert rows is None


def test_mark_to_market_missing_cache_fail_open(tmp_path):
    book = _write_book(tmp_path, {"SPY": 1.0})
    assert g.mark_to_market(basket_path=book,
                            cache_path=tmp_path / "nope.json") is None


def test_mark_to_market_partial_universe_dropped(tmp_path):
    # book holds a ticker absent from the price store → no common dates → None
    cache = _write_cache(tmp_path)
    book = _write_book(tmp_path, {"SPY": 0.5, "GLD": 0.5})
    assert g.mark_to_market(basket_path=book, cache_path=cache) is None


def test_persist_returns_uses_ns8_strategy_id(monkeypatch):
    import types, sys
    captured = {}
    fake_db = types.ModuleType("common.db")
    def _write(strategy_id, rows):
        captured["id"] = strategy_id
        captured["rows"] = rows
        return True
    fake_db.write_strategy_returns = _write
    fake_pkg = types.ModuleType("common")
    fake_pkg.db = fake_db
    monkeypatch.setitem(sys.modules, "common", fake_pkg)
    monkeypatch.setitem(sys.modules, "common.db", fake_db)
    ok = g.persist_returns([{"date": "2026-08-22", "return": 0.01,
                             "source": "ns8_book_mtm"}])
    assert ok is True
    assert captured["id"] == "ns8"
    assert captured["rows"][0]["source"] == "ns8_book_mtm"
