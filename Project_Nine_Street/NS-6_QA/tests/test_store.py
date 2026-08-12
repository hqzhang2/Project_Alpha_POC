"""Test store.py — SQLite drawdown_log with temp-DB isolation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import store


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Redirect DB_PATH to a temp DB before init_db (per-test isolation)."""
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    store.init_db()


def test_init_idempotent():
    store.init_db()  # second call must not raise
    store.init_db()


def test_upsert_and_query(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    store.init_db()
    store.upsert_drawdown("2026-08-11", -0.04, -0.02, -0.05, 0.6, 0.6)
    rows = store.query_window(10)
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-08-11"
    assert rows[0]["budget_remaining_pct"] == pytest.approx(0.6)


def test_upsert_idempotent_same_date(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    store.init_db()
    store.upsert_drawdown("2026-08-11", -0.04, -0.02, -0.05, 0.6, 0.6)
    store.upsert_drawdown("2026-08-11", -0.06, -0.03, -0.05, 0.4, 0.4)
    rows = store.query_window(10)
    assert len(rows) == 1  # replaced, not duplicated
    assert rows[0]["budget_remaining_pct"] == pytest.approx(0.4)


def test_latest_none_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    store.init_db()
    assert store.latest() is None


def test_latest_returns_newest(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    store.init_db()
    store.upsert_drawdown("2026-08-10", -0.04, -0.02, -0.05, 0.6, 0.6)
    store.upsert_drawdown("2026-08-11", -0.05, -0.03, -0.05, 0.4, 0.4)
    latest = store.latest()
    assert latest["date"] == "2026-08-11"


def test_query_window_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    store.init_db()
    for i in range(5):
        store.upsert_drawdown(f"2026-08-{10-i:02d}", -0.04, -0.02, -0.05, 0.5, 0.5)
    rows = store.query_window(2)
    assert len(rows) == 2


def test_breaker_log(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    store.init_db()
    store.log_circuit_breaker("hard_floor", None, "dd 90% of budget")
    store.log_circuit_breaker("position_stop", "AAPL", "AAPL -25%")
    logs = store.query_breakers()
    assert len(logs) == 2
    assert logs[0]["breaker_type"] == "position_stop"  # newest first
    assert logs[0]["ticker"] == "AAPL"
