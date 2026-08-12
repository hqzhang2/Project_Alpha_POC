"""
store.py — NS-6 SQLite history store (drawdown_log, multiplier log).

Follows sentiment_db / regime_store pattern: fail-open, INSERT OR REPLACE
idempotent upsert, query_window(days), latest(). DB at <this-dir>/data/ns6.db.

Tests MUST redirect DB_PATH to a temp dir (monkeypatch) before init_db().
"""

import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import config

log = logging.getLogger("ns6.store")

DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "ns6.db"


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if absent. Idempotent."""
    try:
        with _connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS drawdown_log (
                    date           TEXT PRIMARY KEY,
                    spy_dd_pct     REAL,
                    portfolio_dd_pct REAL,
                    budget_pct     REAL,
                    budget_remaining_pct REAL,
                    multiplier     REAL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS circuit_breaker_log (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp    TEXT,
                    breaker_type TEXT,
                    ticker       TEXT,
                    detail       TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
    except Exception as exc:  # noqa: BLE001 — fail-open
        log.warning("init_db failed: %s", exc)


def upsert_drawdown(date: str, spy_dd, portfolio_dd, budget, remaining, multiplier) -> None:
    """Upsert one drawdown snapshot row (idempotent on date)."""
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO drawdown_log
                (date, spy_dd_pct, portfolio_dd_pct, budget_pct,
                 budget_remaining_pct, multiplier)
                VALUES (?,?,?,?,?,?)
                """,
                (date, spy_dd, portfolio_dd, budget, remaining, multiplier),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("upsert_drawdown failed: %s", exc)


def query_window(days: int = 30) -> List[Dict]:
    """Return the last N days of drawdown history (newest first)."""
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT * FROM drawdown_log ORDER BY date DESC LIMIT ?", (days,)
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        log.warning("query_window failed: %s", exc)
        return []


def latest() -> Optional[Dict]:
    """Most recent drawdown row, or None."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT * FROM drawdown_log ORDER BY date DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None
    except Exception as exc:  # noqa: BLE001
        log.warning("latest failed: %s", exc)
        return None


def log_circuit_breaker(breaker_type: str, ticker: Optional[str], detail: str) -> None:
    """Append a circuit-breaker event to the log."""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO circuit_breaker_log (timestamp, breaker_type, ticker, detail) "
                "VALUES (?,?,?,?)",
                (datetime.now().isoformat(), breaker_type, ticker, detail),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("log_circuit_breaker failed: %s", exc)


def query_breakers(limit: int = 50) -> List[Dict]:
    """Most recent circuit-breaker events (newest first)."""
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT * FROM circuit_breaker_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        log.warning("query_breakers failed: %s", exc)
        return []


# ── Settings (active profile persistence) ───────────────────────────────
ACTIVE_PROFILE_KEY = "active_profile"
DEFAULT_PROFILE = "balanced"


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    """Read a settings row, or None/default if absent. Fail-open."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default
    except Exception as exc:  # noqa: BLE001
        log.warning("get_setting(%s) failed: %s", key, exc)
        return default


def set_setting(key: str, value: str) -> None:
    """Upsert a settings row. Fail-open (log, don't raise)."""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
                (key, value),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("set_setting(%s) failed: %s", key, exc)


def get_active_profile() -> str:
    """Persisted active profile, defaulting to DEFAULT_PROFILE."""
    p = get_setting(ACTIVE_PROFILE_KEY)
    if p and p in config.PROFILES:
        return p
    return DEFAULT_PROFILE


def set_active_profile(name: str) -> str:
    """Persist the active profile. Returns the normalized valid name.

    Invalid name is refused (returns current active) — callers validate
    against config.PROFILES before persisting via this helper's guard.
    """
    if name not in config.PROFILES:
        log.warning("set_active_profile refused unknown profile '%s'", name)
        return get_active_profile()
    set_setting(ACTIVE_PROFILE_KEY, name)
    return name


# ── Portfolio source (decoupled from NS-5) ──────────────────────────────
# The drawdown cockpit's portfolio source. "model" = per-profile model
# portfolio; otherwise an NS-5 portfolio NAME (read from NS-5's
# portfolios.json on demand — no import, no HTTP).
PORTFOLIO_SOURCE_KEY = "portfolio_source"
MODEL_SOURCE = "model"


def get_portfolio_source() -> str:
    """Persisted portfolio source. 'model' or an NS-5 portfolio name.

    Defaults to MODEL_SOURCE. If a stored name no longer exists in NS-5's
    store, callers fall back to model (handled at read time in qa_server).
    """
    p = get_setting(PORTFOLIO_SOURCE_KEY)
    return p if p else MODEL_SOURCE


def set_portfolio_source(name: str) -> str:
    """Persist the portfolio source. 'model' or any non-empty name."""
    name = (name or "").strip()
    if not name:
        name = MODEL_SOURCE
    set_setting(PORTFOLIO_SOURCE_KEY, name)
    return name
