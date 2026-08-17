"""store.py — NS-7 league tenure + grace-clock persistence.

Follows the NS-6 store.py pattern (sentiment_db / regime_store): module-relative
SQLite path, fail-open, INSERT OR REPLACE idempotent upsert. The league state
MUST persist across restarts — the 90-day grace clock is meaningless if a
restart resets the consecutive-compliant counters.

Tests MUST redirect DB_PATH to a temp dir (monkeypatch) before init_db().
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import config

DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "ns7.db"
DEFAULT_DB_PATH = DB_PATH                       # the un-monkeypatched prod path

# Repo root so `import common.db` resolves (this service runs with NS-7_QA/ cwd).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _use_pg() -> bool:
    """True when DB_PATH is the prod default (→ delegate to PostgreSQL common.db).

    Tests monkeypatch store.DB_PATH to a temp sqlite file; that makes this False,
    so the sqlite implementation below remains the hermetic test seam. In prod
    the DB_PATH is untouched → we centralize on Postgres. Fail-open: pg error →
    sqlite fallback so NS-7 never loses league/volume state.
    """
    return DB_PATH == DEFAULT_DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS league (
    ticker          TEXT PRIMARY KEY,
    league          TEXT NOT NULL,
    consecutive_compliant     INTEGER NOT NULL DEFAULT 0,
    consecutive_noncompliant  INTEGER NOT NULL DEFAULT 0,
    first_seen      TEXT NOT NULL,      -- ISO date the ticker entered tracking
    last_seen       TEXT NOT NULL       -- ISO date of the last transition
);

CREATE TABLE IF NOT EXISTS volume (
    ticker          TEXT NOT NULL,
    date            TEXT NOT NULL,
    volume          REAL NOT NULL,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS selection (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at    TEXT NOT NULL,
    as_of           TEXT NOT NULL,
    payload         TEXT NOT NULL       -- JSON: the /api/select document
);

CREATE TABLE IF NOT EXISTS refresh_meta (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL
);
"""


def init_db() -> None:
    if _use_pg():
        try:
            import common.db
            common.db.ensure_schema()
        except Exception:
            pass  # fail-open
        return
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
    if _use_pg():
        try:
            import common.db
            common.db.upsert_league(ticker, league, consecutive_compliant,
                                    consecutive_noncompliant, first_seen, last_seen)
        except Exception:
            pass  # fall through to sqlite
        return
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
    if _use_pg():
        try:
            import common.db
            row = common.db.get_league(ticker)
            if row is not None:
                return row
            return None
        except Exception:
            pass  # fall through to sqlite
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
    if _use_pg():
        try:
            import common.db
            return common.db.league_counts()
        except Exception:
            pass  # fall through to sqlite
    conn = _connect()
    try:
        cur = conn.execute("SELECT league, COUNT(*) FROM league GROUP BY league")
        rows = cur.fetchall()
    finally:
        conn.close()
    return {league: count for league, count in rows}


def all_leagues() -> List[dict]:
    """Full league table (for /api/universe)."""
    if _use_pg():
        try:
            import common.db
            return common.db.all_leagues()
        except Exception:
            pass  # fall through to sqlite
    conn = _connect()
    try:
        cur = conn.execute("SELECT * FROM league ORDER BY ticker")
        rows = cur.fetchall()
    finally:
        conn.close()
    cols = ["ticker", "league", "consecutive_compliant",
            "consecutive_noncompliant", "first_seen", "last_seen"]
    return [dict(zip(cols, r)) for r in rows]


# ── Volume store (U3 liquidity gate) ────────────────────────────────────
# The A_T price store carries closes only; NS-7 keeps its own daily volume
# (fetched by pipeline.py via yfinance). Fail-open discipline lives in the
# pipeline: a systemic volume outage waives U3 for that refresh (don't churn
# the book on a data outage); per-ticker missing volume = not compliant.

def upsert_volume_many(rows: List[tuple]) -> int:
    """rows: [(ticker, date, volume)] — idempotent upsert. Returns count."""
    if not rows:
        return 0
    if _use_pg():
        try:
            import common.db
            return common.db.upsert_volume_many(rows)
        except Exception:
            pass  # fall through to sqlite
    conn = _connect()
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO volume (ticker, date, volume) VALUES (?,?,?)",
            [(t.upper(), d, float(v)) for t, d, v in rows],
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def volume_series(ticker: str, start: str, end: str) -> List[tuple]:
    """[(date, volume)] for one ticker in [start, end], ascending."""
    if _use_pg():
        try:
            import common.db
            return common.db.volume_series(ticker, start, end)
        except Exception:
            pass  # fall through to sqlite
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT date, volume FROM volume WHERE ticker = ? AND date BETWEEN ? AND ? "
            "ORDER BY date", (ticker.upper(), start, end))
        return [(d, v) for d, v in cur.fetchall()]
    finally:
        conn.close()


def avg_daily_volume(ticker: str, as_of: str, window_days: int) -> Optional[float]:
    """20-day average daily volume ending on/before as_of. None if empty."""
    if _use_pg():
        try:
            import common.db
            return common.db.avg_daily_volume(ticker, as_of, window_days)
        except Exception:
            pass  # fall through to sqlite
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT AVG(volume) FROM ("
            "  SELECT volume FROM volume WHERE ticker = ? AND date <= ? "
            "  ORDER BY date DESC LIMIT ?)",
            (ticker.upper(), as_of, window_days))
        row = cur.fetchone()
        return float(row[0]) if row and row[0] is not None else None
    finally:
        conn.close()


def volume_coverage(ticker: str) -> tuple:
    """(min_date, max_date, count) for one ticker's volume rows."""
    if _use_pg():
        try:
            import common.db
            return common.db.volume_coverage(ticker)
        except Exception:
            pass  # fall through to sqlite
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT MIN(date), MAX(date), COUNT(*) FROM volume WHERE ticker = ?",
            (ticker.upper(),))
        row = cur.fetchone()
        return (row[0], row[1], row[2])
    finally:
        conn.close()


# ── Selection persistence (the NS-5 feed) ───────────────────────────────
def save_selection(as_of: str, payload: dict) -> int:
    """Persist the /api/select document. Returns the new row id."""
    if _use_pg():
        try:
            import common.db
            return common.db.save_selection(as_of, payload)
        except Exception:
            pass  # fall through to sqlite
    import json as _json
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO selection (generated_at, as_of, payload) VALUES (?,?,?)",
            (datetime.now(timezone.utc).isoformat(), as_of,
             _json.dumps(payload, default=str)))
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        conn.close()


def latest_selection() -> Optional[dict]:
    """Most recent selection document: {generated_at, as_of, payload(dict)}."""
    if _use_pg():
        try:
            import common.db
            return common.db.latest_selection()
        except Exception:
            pass  # fall through to sqlite
    import json as _json
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT generated_at, as_of, payload FROM selection "
            "ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    generated_at, as_of, payload = row
    try:
        return {"generated_at": generated_at, "as_of": as_of,
                "payload": _json.loads(payload)}
    except (ValueError, TypeError):
        return {"generated_at": generated_at, "as_of": as_of, "payload": {}}


# ── Refresh meta (last-run stamps for /health + diagnostics) ────────────
def set_meta(key: str, value: str) -> None:
    if _use_pg():
        try:
            import common.db
            common.db.set_meta(key, value)
        except Exception:
            pass  # fall through to sqlite
        return
    conn = _connect()
    try:
        conn.execute("INSERT OR REPLACE INTO refresh_meta (key, value) VALUES (?,?)",
                     (key, value))
        conn.commit()
    finally:
        conn.close()


def get_meta(key: str) -> Optional[str]:
    if _use_pg():
        try:
            import common.db
            return common.db.get_meta(key)
        except Exception:
            pass  # fall through to sqlite
    conn = _connect()
    try:
        cur = conn.execute("SELECT value FROM refresh_meta WHERE key = ?", (key,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()
