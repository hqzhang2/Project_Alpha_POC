"""
NS-5 Regime History Store — SQLite storage for regime classifications.

Reuses the sentiment_db.py shape and pattern. Fail-open: no crash on missing DB.

Table: regime_history(date TEXT PK, regime TEXT, confidence REAL, flags TEXT,
       cpi_yoy REAL, gdp_qoq REAL, unrate REAL, curve_bp REAL, baa_aaa_bp REAL,
       nfci REAL, vix REAL, corr REAL, wti REAL, recorded_at TEXT)

JUNIOR (cheap model): mechanics only.
"""
from __future__ import annotations

import datetime
import os
import sqlite3

import pandas as pd

# Store in common/data/ alongside sentiment_db (pattern match)
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DATA_DIR, "regime_history.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS regime_history (
    date        TEXT PRIMARY KEY,       -- YYYY-MM-DD
    regime      TEXT NOT NULL,          -- R1 | R2 | R3 | R4
    confidence  REAL,
    flags       TEXT,                   -- comma-separated
    cpi_yoy     REAL,
    gdp_qoq     REAL,
    unrate      REAL,
    curve_bp    REAL,
    baa_aaa_bp  REAL,
    nfci        REAL,
    vix         REAL,
    corr        REAL,
    wti         REAL,
    recorded_at TEXT NOT NULL           -- UTC timestamp
);
"""


def _connect() -> sqlite3.Connection:
    """Get a DB connection (creates dir + DB if missing)."""
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create table if missing. Idempotent; call at import time."""
    try:
        with _connect() as conn:
            conn.executescript(_SCHEMA)
    except Exception:
        pass  # fail-open


def upsert(date: str, row: dict) -> bool:
    """Insert or replace a regime row by date. Returns True on success.

    Args:
        date: YYYY-MM-DD string
        row: dict with keys {regime, confidence, flags, cpi_yoy, gdp_qoq,
             unrate, curve_bp, baa_aaa_bp, nfci, vix, corr, wti}
    """
    try:
        recorded_at = datetime.datetime.utcnow().isoformat() + "Z"
        with _connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO regime_history "
                "(date, regime, confidence, flags, cpi_yoy, gdp_qoq, unrate, "
                " curve_bp, baa_aaa_bp, nfci, vix, corr, wti, recorded_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    date,
                    row.get("regime"),
                    row.get("confidence"),
                    row.get("flags"),
                    row.get("cpi_yoy"),
                    row.get("gdp_qoq"),
                    row.get("unrate"),
                    row.get("curve_bp"),
                    row.get("baa_aaa_bp"),
                    row.get("nfci"),
                    row.get("vix"),
                    row.get("corr"),
                    row.get("wti"),
                    recorded_at,
                ),
            )
        return True
    except Exception:
        return False


def query_window(days: int = 750) -> pd.DataFrame:
    """Return trailing N days of regime history as a DataFrame.

    Returns DataFrame with 'date' as index, or empty DataFrame on failure.
    """
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT * FROM regime_history ORDER BY date DESC LIMIT ?",
                (days,),
            ).fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame([dict(r) for r in rows])
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        return df
    except Exception:
        return pd.DataFrame()


def latest() -> dict | None:
    """Return the most recent regime row as a dict, or None."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT * FROM regime_history ORDER BY date DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return dict(row)
    except Exception:
        return None


# Seed at import so the table is always present.
init_db()
