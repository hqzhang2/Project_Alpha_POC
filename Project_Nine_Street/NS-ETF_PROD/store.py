"""NS-ETF store — sqlite persistence for prices, signals, and refresh meta."""
import json
import sqlite3
import time
from pathlib import Path

import config

DB_PATH = config.DATA_DIR / "nsetf.sqlite"


def _connect(path=None):
    p = Path(path) if path else DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path=None):
    conn = _connect(path)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS prices (
        ticker TEXT NOT NULL,
        date   TEXT NOT NULL,
        close  REAL NOT NULL,
        high   REAL,
        low    REAL,
        PRIMARY KEY (ticker, date)
    );
    CREATE TABLE IF NOT EXISTS signals (
        as_of      TEXT NOT NULL,
        ticker     TEXT NOT NULL,
        sleeve     TEXT NOT NULL,
        score      REAL NOT NULL,
        signal     INTEGER NOT NULL,
        weight     REAL NOT NULL,
        PRIMARY KEY (as_of, ticker)
    );
    CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """)
    conn.commit()
    conn.close()


def upsert_prices(conn, ticker, rows):
    """rows: list of (date_iso, close, high, low) oldest-first."""
    conn.executemany(
        "INSERT OR REPLACE INTO prices (ticker, date, close, high, low) "
        "VALUES (?, ?, ?, ?, ?)",
        [(ticker, d, c, h, l) for d, c, h, l in rows])
    conn.commit()


def series(conn, ticker, field="close", limit=None):
    q = f"SELECT date, {field} FROM prices WHERE ticker=? ORDER BY date"
    if limit:
        q += f" DESC LIMIT {int(limit)}"
    rows = conn.execute(q, (ticker,)).fetchall()
    rows.reverse()
    return [r[1] for r in rows]


def save_signals(conn, as_of, rows):
    """rows: (ticker, sleeve, score, signal, weight)"""
    conn.executemany(
        "INSERT OR REPLACE INTO signals "
        "(as_of, ticker, sleeve, score, signal, weight) VALUES (?,?,?,?,?,?)",
        [(as_of, t, s, sc, sg, w) for t, s, sc, sg, w in rows])
    conn.commit()


def set_meta(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                 (key, json.dumps(value)))
    conn.commit()


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    if row is None:
        return default
    return json.loads(row[0])
