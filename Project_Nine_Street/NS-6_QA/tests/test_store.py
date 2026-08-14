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


# ── Settings / active profile persistence ───────────────────────────────
def test_get_setting_default():
    assert store.get_setting("nope", "fallback") == "fallback"


def test_set_get_setting_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    store.init_db()
    store.set_setting("active_profile", "growth")
    assert store.get_setting("active_profile") == "growth"


def test_active_profile_default_is_balanced(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    store.init_db()
    assert store.get_active_profile() == "balanced"


def test_set_active_profile_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    store.init_db()
    store.set_active_profile("capital_preservation")
    assert store.get_active_profile() == "capital_preservation"


def test_set_active_profile_rejects_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    store.init_db()
    # unknown name refused → stays at default
    assert store.set_active_profile("nope") == "balanced"
    assert store.get_active_profile() == "balanced"


# ── R3: vix_level column + crisis-mode state ─────────────────────────────
def test_upsert_vix_level_and_latest(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    store.init_db()
    store.upsert_drawdown("2026-08-13", -0.04, -0.02, -0.05, 0.6, 0.6, vix_level=25.5)
    assert store.latest()["vix_level"] == pytest.approx(25.5)


def test_migration_adds_vix_column_to_existing_table(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "old.db")
    conn = sqlite3.connect(str(store.DB_PATH))
    conn.execute("CREATE TABLE drawdown_log (date TEXT PRIMARY KEY, spy_dd_pct REAL, "
                 "portfolio_dd_pct REAL, budget_pct REAL, budget_remaining_pct REAL, multiplier REAL)")
    conn.commit()
    conn.close()
    store.init_db()  # migration must add vix_level to a pre-existing table
    cols = {r[1] for r in sqlite3.connect(str(store.DB_PATH)).execute("PRAGMA table_info(drawdown_log)")}
    assert "vix_level" in cols
    # and an upsert with vix works against the migrated table
    store.upsert_drawdown("2026-08-13", -0.04, -0.02, -0.05, 0.6, 0.6, vix_level=20.0)
    assert store.latest()["vix_level"] == pytest.approx(20.0)


def test_crisis_mode_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    store.init_db()
    assert store.get_crisis_mode() is False
    store.set_crisis_mode(True)
    assert store.get_crisis_mode() is True
    store.set_crisis_mode(False)
    assert store.get_crisis_mode() is False
