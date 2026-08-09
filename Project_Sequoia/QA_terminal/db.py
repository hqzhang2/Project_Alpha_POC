"""
SQLite storage for Alpha Terminal 52-week-high snapshots.

Table `year_highs` is partitioned by date so the same date can be refreshed.
All functions are thread-safe (sqlite3 `check_same_thread` handled via
per-call connections) and use the stdlib only.

Schema:
    year_highs(
        date        TEXT,   -- YYYY-MM-DD (EST/EDT close date)
        ticker      TEXT,
        exchange    TEXT,   -- NYSE / NASDAQ
        sector      TEXT,
        close       REAL,   -- most recent close
        high_52w    REAL,   -- 52-week high (trailing 252-day high)
        pct_off     REAL,   -- (close - high_52w) / high_52w * 100  (0 = at high)
        volume      REAL,
        PRIMARY KEY (date, ticker)
    )
"""
import os
import sqlite3
from datetime import date, datetime

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "alpha_terminal.db")


def _connect():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create the year_highs and year_lows tables if they do not exist."""
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS year_highs (
                date        TEXT NOT NULL,
                ticker      TEXT NOT NULL,
                exchange    TEXT,
                sector      TEXT,
                company     TEXT,
                close       REAL,
                high_52w    REAL,
                pct_off     REAL,
                volume      REAL,
                market_cap  REAL,
                PRIMARY KEY (date, ticker)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_year_highs_date ON year_highs(date)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_year_highs_ticker ON year_highs(ticker)"
        )
        # Backfill company column on DBs created before it existed
        try:
            conn.execute("ALTER TABLE year_highs ADD COLUMN company TEXT")
        except Exception:
            pass  # column already present
        # Backfill market_cap column
        try:
            conn.execute("ALTER TABLE year_highs ADD COLUMN market_cap REAL")
        except Exception:
            pass  # column already present

        # year_lows table (mirror of year_highs with low_52w / pct_from_low)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS year_lows (
                date         TEXT NOT NULL,
                ticker       TEXT NOT NULL,
                exchange     TEXT,
                sector       TEXT,
                company      TEXT,
                close        REAL,
                low_52w      REAL,
                pct_from_low REAL,
                volume       REAL,
                market_cap   REAL,
                PRIMARY KEY (date, ticker)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_year_lows_date ON year_lows(date)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_year_lows_ticker ON year_lows(ticker)"
        )
        conn.commit()
    finally:
        conn.close()


def store_year_highs(date_str, rows):
    """Replace (upsert) all rows for a given date.

    rows: iterable of dicts with keys:
        ticker, exchange, sector, close, high_52w, pct_off, volume, market_cap
    """
    init_db()
    conn = _connect()
    try:
        conn.execute("DELETE FROM year_highs WHERE date = ?", (date_str,))
        conn.executemany(
            """
            INSERT INTO year_highs
                (date, ticker, exchange, sector, company, close, high_52w, pct_off, volume, market_cap)
            VALUES
                (:date, :ticker, :exchange, :sector, :company, :close, :high_52w, :pct_off, :volume, :market_cap)
            """,
            # company/market_cap are nullable and may be absent from caller
            # rows (test fixtures, partial builds) — default them so the
            # named-param binding never errors (was "binding 5" ProgrammingError).
            [{**{"date": date_str, "company": "", "market_cap": None}, **r}
             for r in rows],
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def get_year_highs(date_str):
    """Return all rows for a date, sorted by pct_off asc (closest to high first)."""
    init_db()
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT * FROM year_highs WHERE date = ? ORDER BY pct_off ASC, ticker ASC",
            (date_str,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def search_year_highs(date_str, query):
    """Case-insensitive substring search on ticker within a date's snapshot."""
    init_db()
    conn = _connect()
    try:
        like = f"%{query.upper()}%"
        cur = conn.execute(
            """
            SELECT * FROM year_highs
            WHERE date = ? AND (ticker LIKE ? OR sector LIKE ?)
            ORDER BY pct_off ASC, ticker ASC
            """,
            (date_str, like, like),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def store_year_lows(date_str, rows):
    """Replace (upsert) all rows for a given date.

    rows: iterable of dicts with keys:
        ticker, exchange, sector, close, low_52w, pct_from_low, volume, market_cap
    """
    init_db()
    conn = _connect()
    try:
        conn.execute("DELETE FROM year_lows WHERE date = ?", (date_str,))
        conn.executemany(
            """
            INSERT INTO year_lows
                (date, ticker, exchange, sector, company, close, low_52w, pct_from_low, volume, market_cap)
            VALUES
                (:date, :ticker, :exchange, :sector, :company, :close, :low_52w, :pct_from_low, :volume, :market_cap)
            """,
            [{**{"date": date_str, "company": "", "market_cap": None}, **r}
             for r in rows],
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def get_year_lows(date_str):
    """Return all rows for a date, sorted by pct_from_low asc."""
    init_db()
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT * FROM year_lows WHERE date = ? ORDER BY pct_from_low ASC, ticker ASC",
            (date_str,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def search_year_lows(date_str, query):
    """Case-insensitive substring search on ticker within a date's snapshot."""
    init_db()
    conn = _connect()
    try:
        like = f"%{query.upper()}%"
        cur = conn.execute(
            """
            SELECT * FROM year_lows
            WHERE date = ? AND (ticker LIKE ? OR sector LIKE ?)
            ORDER BY pct_from_low ASC, ticker ASC
            """,
            (date_str, like, like),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def list_dates():
    """Distinct snapshot dates from year_highs, newest first."""
    init_db()
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT DISTINCT date FROM year_highs ORDER BY date DESC"
        )
        return [r["date"] for r in cur.fetchall()]
    finally:
        conn.close()


def get_sector_trend(table_name, pct_col, op):
    """Per-date sector counts for the trend chart.

    table_name: 'year_highs' or 'year_lows' (whitelisted — never user input).
    pct_col: 'pct_off' (highs) or 'pct_from_low' (lows).
    op: '>=' (highs) or '<=' (lows) — must match the page's displayed-count
        filter so the trend TOTAL legend equals the page status count.
    Returns rows [{date, sector, count}] sorted by date asc, count desc.
    """
    if table_name not in ("year_highs", "year_lows"):
        raise ValueError(f"bad table: {table_name}")
    if op not in (">=", "<="):
        raise ValueError(f"bad op: {op}")
    init_db()
    conn = _connect()
    try:
        cur = conn.execute(
            f"SELECT date, sector, COUNT(*) AS count "
            f"FROM {table_name} "
            f"WHERE {pct_col} IS NOT NULL AND {pct_col} {op} 0 "
            f"GROUP BY date, sector ORDER BY date ASC, count DESC"
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def list_lows_dates():
    """Distinct snapshot dates from year_lows, newest first."""
    init_db()
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT DISTINCT date FROM year_lows ORDER BY date DESC"
        )
        return [r["date"] for r in cur.fetchall()]
    finally:
        conn.close()


def today_est_str():
    """Today's date as YYYY-MM-DD (EST/EDT)."""
    # Eastern time; use a fixed offset table for DST simplicity via zoneinfo if
    # available, else fall back to UTC-5/-4 by month.
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        return date.today().isoformat()
