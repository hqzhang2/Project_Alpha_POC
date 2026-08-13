"""store.py — NS-7 league tenure + grace-clock persistence.

Follows the NS-6 store.py pattern (sentiment_db / regime_store): module-relative
SQLite path, fail-open, INSERT OR REPLACE idempotent upsert. The league state
MUST persist across restarts — the 90-day grace clock is meaningless if a
restart resets the consecutive-compliant counters.

Tests MUST redirect DB_PATH to a temp dir (monkeypatch) before init_db().
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import List, Optional

import config

DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "ns7.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS league (
    ticker          TEXT PRIMARY KEY,
    league          TEXT NOT NULL,
    consecutive_compliant     INTEGER NOT NULL DEFAULT 0,
    consecutive_noncompliant  INTEGER NOT NULL DEFAULT 0,
    first_seen      TEXT NOT NULL,      -- ISO date the ticker entered tracking
    last_seen       TEXT NOT NULL       -- ISO date of the last transition
);
"""


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(str(DB_PATH))


def upsert_league(ticker: str, league: str, consecutive_compliant: int,
                  consecutive_noncompliant: int, first_seen: str,
                  last_seen: str) -> None:
    """INSERT OR REPLACE one ticker's league row. Idempotent."""
    conn = _connect()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO league
               (ticker, league, consecutive_compliant, consecutive_noncompliant,
                first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ticker.upper(), league, consecutive_compliant,
             consecutive_noncompliant, first_seen, last_seen),
        )
        conn.commit()
    finally:
        conn.close()


def get_league(ticker: str) -> Optional[dict]:
    """Return one ticker's row (or None)."""
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT * FROM league WHERE ticker = ?", (ticker.upper(),)
        )
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    cols = ["ticker", "league", "consecutive_compliant",
            "consecutive_noncompliant", "first_seen", "last_seen"]
    return dict(zip(cols, row))


def league_counts() -> dict:
    """{league: count} across all tracked tickers."""
    conn = _connect()
    try:
        cur = conn.execute("SELECT league, COUNT(*) FROM league GROUP BY league")
        rows = cur.fetchall()
    finally:
        conn.close()
    return {league: count for league, count in rows}


def all_leagues() -> List[dict]:
    """Full league table (for /api/universe)."""
    conn = _connect()
    try:
        cur = conn.execute("SELECT * FROM league ORDER BY ticker")
        rows = cur.fetchall()
    finally:
        conn.close()
    cols = ["ticker", "league", "consecutive_compliant",
            "consecutive_noncompliant", "first_seen", "last_seen"]
    return [dict(zip(cols, r)) for r in rows]
