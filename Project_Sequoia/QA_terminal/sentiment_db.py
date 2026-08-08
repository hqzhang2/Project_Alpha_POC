"""SQLite storage layer for the Alpha Terminal sentiment strip.

Two tables:
  readings           — fact table, one row per (asof_date, scope, ticker, metric, source)
  metric_definitions — interpretation dimension: display, unit, direction, normalization.
                       Single source of truth for how raw `value` becomes `sentiment`
                       (Hong's rule: interpretation lives here, never in collectors/UI).

Fail-open contract: DB init is lazy and non-fatal; missing tables/DB -> empty results.
Natural key + INSERT OR REPLACE = idempotent upsert (re-runs can't duplicate; backfill safe).
"""
import datetime
import os
import sqlite3

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DATA_DIR, "sentiment.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    asof_date   TEXT NOT NULL,          -- YYYY-MM-DD the reading represents (settlement date for lagged sources)
    scope       TEXT NOT NULL,          -- 'market' | 'ticker'
    ticker      TEXT,                   -- NULL for market scope
    metric      TEXT NOT NULL,
    source      TEXT NOT NULL,
    value       REAL,                   -- raw reading, never interpreted
    sentiment   REAL,                   -- -1..+1, +1 = max bullish; NULL when not scored
    count       INTEGER,                -- units behind the reading (contracts/articles/respondents)
    recorded_at TEXT NOT NULL,          -- UTC fetch timestamp
    PRIMARY KEY (asof_date, scope, ticker, metric, source)
);

CREATE TABLE IF NOT EXISTS metric_definitions (
    metric         TEXT PRIMARY KEY,
    display_name   TEXT NOT NULL,
    scope          TEXT NOT NULL,       -- 'market' | 'ticker' (validation)
    unit           TEXT,
    higher_is      TEXT NOT NULL,       -- 'bullish' | 'bearish'
    normalization  TEXT,                -- call_share|percentile|percentile_inv|spread_100|center_50|passthrough|NULL
    window         INTEGER,             -- trailing lookback days for percentile methods
    source_default TEXT
);

-- Per-filing insider detail (drill-down modal). One row per Form 4
-- transaction line; net buy/sell = sum over the ticker's window.
CREATE TABLE IF NOT EXISTS insider_filings (
    ticker      TEXT NOT NULL,
    filing_date TEXT NOT NULL,          -- YYYY-MM-DD (filing date, ~2d after trade)
    insider     TEXT,                   -- reporting owner name
    role        TEXT,                   -- title/role (CFO, Director, ...)
    code        TEXT NOT NULL,          -- P buy | S sell | M exercise | F tax | ...
    shares      REAL,
    price       REAL,
    value       REAL,                   -- shares * price (signed by code)
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (ticker, filing_date, insider, code, shares, price)
);

-- Per-day social breakdown (drill-down modal). One row per (ticker, day).
CREATE TABLE IF NOT EXISTS social_daily (
    ticker     TEXT NOT NULL,
    day        TEXT NOT NULL,           -- YYYY-MM-DD
    messages   INTEGER NOT NULL,        -- total messages that day
    classified INTEGER NOT NULL,        -- with a Bullish/Bearish tag
    bull       INTEGER NOT NULL,
    bear       INTEGER NOT NULL,
    spread     REAL,                    -- (bull - bear) / classified
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (ticker, day)
);
"""


def _connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if missing. Idempotent; call at import time.

    Also dedupes pre-index duplicate readings (older rows removed, newest kept)
    so the unique index can be created on a legacy DB without failing.
    """
    try:
        with _connect() as conn:
            conn.executescript(_SCHEMA)
            # One-time cleanup: keep newest rowid per natural key (NULL-safe).
            conn.execute(
                "DELETE FROM readings WHERE rowid NOT IN ("
                "  SELECT MAX(rowid) FROM readings "
                "  GROUP BY asof_date, scope, COALESCE(ticker, ''), metric, source)"
            )
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_readings_key "
                         "ON readings (asof_date, scope, COALESCE(ticker, ''), metric, source)")
    except Exception:
        pass  # fail-open: storage problems never crash the server


def upsert_reading(asof_date, scope, ticker, metric, source, value, sentiment, count=None, recorded_at=None):
    """Idempotent insert/replace by natural key. Returns True on success (fail-open)."""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO readings "
                "(asof_date, scope, ticker, metric, source, value, sentiment, count, recorded_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    asof_date, scope, ticker, metric, source, value, sentiment, count,
                    recorded_at or datetime.datetime.utcnow().isoformat() + "Z",
                ),
            )
        return True
    except Exception:
        return False


def seed_metrics(definitions):
    """Upsert metric_definitions rows: list of dicts with the table's columns."""
    try:
        with _connect() as conn:
            for d in definitions:
                conn.execute(
                    "INSERT OR REPLACE INTO metric_definitions "
                    "(metric, display_name, scope, unit, higher_is, normalization, window, source_default) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (d["metric"], d["display_name"], d["scope"], d.get("unit"), d["higher_is"],
                     d.get("normalization"), d.get("window"), d.get("source_default")),
                )
        return True
    except Exception:
        return False


def get_metrics():
    """All metric_definitions rows (drives dashboard headers/filters). Fail-open -> []."""
    try:
        with _connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM metric_definitions ORDER BY metric")]
    except Exception:
        return []


def query_readings(scope=None, ticker=None, metric=None, days=None, sources=None, latest=False):
    """Filtered readings join metric_definitions, newest first. Fail-open -> [].

    days: only readings with asof_date >= today-days+1 (uses the latest recorded date
    in the table as the anchor so lagged sources don't collapse the window).
    latest: one row per (scope, ticker, metric, source) = its most recent reading,
    regardless of source lag (NAAIM weeks old, FINRA settlement lag, FRED monthly).
    This is the strip's default view — a current sentiment snapshot, not a log.
    """
    try:
        with _connect() as conn:
            anchor = conn.execute("SELECT MAX(asof_date) FROM readings").fetchone()[0]
            where, args = [], []
            if scope:
                where.append("r.scope = ?"); args.append(scope)
            if ticker:
                where.append("UPPER(r.ticker) = UPPER(?)"); args.append(ticker)
            if metric:
                where.append("r.metric = ?"); args.append(metric)
            if sources:
                marks = ",".join("?" * len(sources))
                where.append(f"r.source IN ({marks})"); args.extend(sources)
            if days and anchor:
                cutoff = (datetime.date.fromisoformat(anchor) - datetime.timedelta(days=days - 1)).isoformat()
                where.append("r.asof_date >= ?"); args.append(cutoff)

            if latest:
                # One row per natural key: join to the max-asof_date per key.
                # Filters apply to the inner latest-select so they narrow the
                # universe BEFORE "latest" is computed.
                inner_where = " AND ".join(where).replace("r.", "x.") if where else "1=1"
                sql = (
                    "SELECT r.asof_date, r.scope, r.ticker, r.metric, r.source, r.value, r.sentiment, "
                    "r.count, r.recorded_at, m.display_name, m.unit, m.higher_is, m.normalization "
                    "FROM readings r "
                    "JOIN (SELECT scope, COALESCE(ticker,'') tk, metric, source, MAX(asof_date) md "
                    "      FROM readings x WHERE " + inner_where + " "
                    "      GROUP BY scope, COALESCE(ticker,''), metric, source) l "
                    "ON r.scope = l.scope AND COALESCE(r.ticker,'') = l.tk "
                    "AND r.metric = l.metric AND r.source = l.source AND r.asof_date = l.md "
                    "LEFT JOIN metric_definitions m ON r.metric = m.metric "
                    "ORDER BY r.asof_date DESC, r.metric, r.ticker"
                )
            else:
                sql = (
                    "SELECT r.asof_date, r.scope, r.ticker, r.metric, r.source, r.value, r.sentiment, "
                    "r.count, r.recorded_at, "
                    "m.display_name, m.unit, m.higher_is, m.normalization "
                    "FROM readings r LEFT JOIN metric_definitions m ON r.metric = m.metric"
                )
                if where:
                    sql += " WHERE " + " AND ".join(where)
                sql += " ORDER BY r.asof_date DESC, r.metric, r.ticker"
            return [dict(r) for r in conn.execute(sql, args)]
    except Exception:
        return []


def history_for(metric, limit=None):
    """Trailing raw values for a metric, oldest->newest (percentile window source).

    Used by collectors whose normalization needs the strip's own accumulated
    history (forward-only sources like CBOE P/C and FINRA short interest).
    """
    try:
        with _connect() as conn:
            sql = "SELECT value FROM readings WHERE metric=? AND value IS NOT NULL ORDER BY asof_date ASC"
            if limit:
                sql = (
                    "SELECT value FROM (SELECT value, asof_date FROM readings "
                    "WHERE metric=? AND value IS NOT NULL ORDER BY asof_date DESC LIMIT ?) "
                    "ORDER BY asof_date ASC"
                )
                rows = conn.execute(sql, (metric, int(limit))).fetchall()
            else:
                rows = conn.execute(sql, (metric,)).fetchall()
            return [r[0] for r in rows]
    except Exception:
        return []


def latest_reading_date():
    """Newest asof_date in the strip (or None). Used as window anchor."""
    try:
        with _connect() as conn:
            return conn.execute("SELECT MAX(asof_date) FROM readings").fetchone()[0]
    except Exception:
        return None


def latest_reading_date_for(metric, source):
    """Newest asof_date for a specific (metric, source) pair (or None).

    Enables incremental collection: only dates newer than this are processed.
    """
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT MAX(asof_date) FROM readings WHERE metric=? AND source=?",
                (metric, source),
            ).fetchone()
            return row[0]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Detail tables (drill-down modals): insider_filings + social_daily
# ---------------------------------------------------------------------------

def replace_filings(ticker, rows):
    """Replace a ticker's insider_filings rows (per-collection-run snapshot).

    rows: list of dicts {filing_date, insider, role, code, shares, price, value}
    Returns row count written, or 0 on failure (fail-open).
    """
    try:
        with _connect() as conn:
            conn.execute("DELETE FROM insider_filings WHERE ticker=?", (ticker,))
            for r in rows:
                conn.execute(
                    "INSERT OR REPLACE INTO insider_filings "
                    "(ticker, filing_date, insider, role, code, shares, price, value, recorded_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (ticker, r.get("filing_date"), r.get("insider"), r.get("role"), r.get("code"),
                     r.get("shares"), r.get("price"), r.get("value"),
                     datetime.datetime.utcnow().isoformat()),
                )
        return len(rows)
    except Exception:
        return 0


def query_filings(ticker, limit=50):
    """A ticker's insider filings, newest first. Fail-open -> []."""
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT filing_date, insider, role, code, shares, price, value "
                "FROM insider_filings WHERE ticker=? ORDER BY filing_date DESC LIMIT ?",
                (ticker, int(limit)),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def replace_social_daily(ticker, rows):
    """Replace a ticker's social_daily rows (per-collection-run snapshot).

    rows: list of dicts {day, messages, classified, bull, bear, spread}
    Returns row count written, or 0 on failure (fail-open).
    """
    try:
        with _connect() as conn:
            conn.execute("DELETE FROM social_daily WHERE ticker=?", (ticker,))
            for r in rows:
                conn.execute(
                    "INSERT OR REPLACE INTO social_daily "
                    "(ticker, day, messages, classified, bull, bear, spread, recorded_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (ticker, r.get("day"), r.get("messages", 0), r.get("classified", 0),
                     r.get("bull", 0), r.get("bear", 0), r.get("spread"),
                     datetime.datetime.utcnow().isoformat()),
                )
        return len(rows)
    except Exception:
        return 0


def query_social_daily(ticker, limit=14):
    """A ticker's per-day social breakdown, newest first. Fail-open -> []."""
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT day, messages, classified, bull, bear, spread "
                "FROM social_daily WHERE ticker=? ORDER BY day DESC LIMIT ?",
                (ticker, int(limit)),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


# Seed at import so the definitions table is always present.
init_db()
