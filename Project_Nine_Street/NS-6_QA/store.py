"""
store.py — NS-6 SQLite history store (drawdown_log, multiplier log).

Follows sentiment_db / regime_store pattern: fail-open, INSERT OR REPLACE
idempotent upsert, query_window(days), latest(). DB at NS-6_QA/data/ns6.db.

Tests MUST redirect DB_PATH to a temp dir (monkeypatch) before init_db().
"""

import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import config

log = logging.getLogger("ns6.store")

DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "ns6.db"
DEFAULT_DB_PATH = DB_PATH                       # the un-monkeypatched prod path

# Repo root so `import common.db` resolves (this service runs with NS-6_QA/ cwd).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _use_pg() -> bool:
    """True when DB_PATH is the prod default (→ delegate to PostgreSQL common.db).

    Tests monkeypatch store.DB_PATH to a temp sqlite file; that makes this False,
    so the sqlite implementation below remains the hermetic test seam. In prod
    the DB_PATH is untouched → we centralize on Postgres. Fail-open: if common.db
    is unreachable we fall back to sqlite so NS-6 never loses enforcement state.
    """
    return DB_PATH == DEFAULT_DB_PATH


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if absent. Idempotent. Prod: ensure Postgres schema."""
    if _use_pg():
        try:
            import common.db
            common.db.ensure_schema()
        except Exception as exc:  # noqa: BLE001
            log.warning("ensure_schema failed: %s", exc)
        return
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
                    multiplier     REAL,
                    vix_level      REAL,
                    position_drawdowns TEXT,
                    cross_sectional_corr REAL
                )
                """
            )
            # R3 migration: add vix_level to pre-existing drawdown_log tables
            # (SQLite has no ADD COLUMN IF NOT EXISTS; check PRAGMA).
            cols = {r[1] for r in conn.execute("PRAGMA table_info(drawdown_log)")}
            if "vix_level" not in cols:
                conn.execute("ALTER TABLE drawdown_log ADD COLUMN vix_level REAL")
            # G1 migration: per-ticker drawdowns (JSON) + cross-sectional corr
            # so the enforcement loop can evaluate breakers/stops on real data
            # (persisted by the feed, read by the GET — never recomputed live).
            if "position_drawdowns" not in cols:
                conn.execute("ALTER TABLE drawdown_log ADD COLUMN position_drawdowns TEXT")
            if "cross_sectional_corr" not in cols:
                conn.execute("ALTER TABLE drawdown_log ADD COLUMN cross_sectional_corr REAL")
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS performance_log (
                    date          TEXT PRIMARY KEY,
                    nav           REAL,
                    ret           REAL,
                    spy_ret       REAL,
                    universe_ret  REAL,
                    contributions TEXT
                )
                """
            )
    except Exception as exc:  # noqa: BLE001 — fail-open
        log.warning("init_db failed: %s", exc)


def upsert_drawdown(date: str, spy_dd, portfolio_dd, budget, remaining, multiplier,
                    vix_level=None, position_drawdowns=None,
                    cross_sectional_corr=None) -> None:
    """Upsert one drawdown snapshot row (idempotent on date). Prod: Postgres."""
    if _use_pg():
        try:
            import common.db
            common.db.upsert_drawdown(date, spy_dd, portfolio_dd, budget, remaining,
                                      multiplier, vix_level, position_drawdowns,
                                      cross_sectional_corr)
        except Exception as exc:  # noqa: BLE001
            log.warning("pg upsert_drawdown failed (fallback sqlite): %s", exc)
            return _sqlite_upsert_drawdown(date, spy_dd, portfolio_dd, budget,
                                           remaining, multiplier, vix_level,
                                           position_drawdowns, cross_sectional_corr)
        return
    return _sqlite_upsert_drawdown(date, spy_dd, portfolio_dd, budget, remaining,
                                   multiplier, vix_level, position_drawdowns,
                                   cross_sectional_corr)


def _sqlite_upsert_drawdown(date: str, spy_dd, portfolio_dd, budget, remaining,
                            multiplier, vix_level=None, position_drawdowns=None,
                            cross_sectional_corr=None) -> None:
    if isinstance(position_drawdowns, dict):
        position_drawdowns = json.dumps(position_drawdowns)
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO drawdown_log
                (date, spy_dd_pct, portfolio_dd_pct, budget_pct,
                 budget_remaining_pct, multiplier, vix_level,
                 position_drawdowns, cross_sectional_corr)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (date, spy_dd, portfolio_dd, budget, remaining, multiplier,
                 vix_level, position_drawdowns, cross_sectional_corr),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("upsert_drawdown failed: %s", exc)


def query_window(days: int = 30) -> List[Dict]:
    """Return the last N days of drawdown history (newest first). Prod: Postgres."""
    if _use_pg():
        try:
            import common.db
            rows = common.db.query_drawdown(days)
            return [dict(r) for r in rows]
        except Exception as exc:  # noqa: BLE001
            log.warning("pg query_drawdown failed (fallback sqlite): %s", exc)
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
    """Most recent drawdown row, or None. Prod: Postgres."""
    if _use_pg():
        try:
            import common.db
            row = common.db.latest_drawdown()
            return dict(row) if row else None
        except Exception as exc:  # noqa: BLE001
            log.warning("pg latest_drawdown failed (fallback sqlite): %s", exc)
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
    """Append a circuit-breaker event to the log. Prod: Postgres."""
    if _use_pg():
        try:
            import common.db
            common.db.log_circuit_breaker(breaker_type, ticker, detail)
        except Exception as exc:  # noqa: BLE001
            log.warning("pg log_circuit_breaker failed (fallback sqlite): %s", exc)
            return _sqlite_log_circuit_breaker(breaker_type, ticker, detail)
        return
    return _sqlite_log_circuit_breaker(breaker_type, ticker, detail)


def _sqlite_log_circuit_breaker(breaker_type: str, ticker: Optional[str], detail: str) -> None:
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
    """Most recent circuit-breaker events (newest first). Prod: Postgres."""
    if _use_pg():
        try:
            import common.db
            return [dict(r) for r in common.db.query_breakers(limit)]
        except Exception as exc:  # noqa: BLE001
            log.warning("pg query_breakers failed (fallback sqlite): %s", exc)
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


def last_breaker_time() -> Optional[str]:
    """ISO timestamp of the most recent hard_floor/systemic_event breaker.

    Used by the enforcement loop's re-entry hysteresis (G1). Position stops
    are excluded — those are tracked per-ticker via last_stop_times().
    Returns None when no such breaker has fired.
    """
    for row in query_breakers(limit=200):
        if row.get("breaker_type") in ("hard_floor", "systemic_event"):
            return row.get("timestamp")
    return None


def last_stop_times() -> Dict[str, str]:
    """{ticker: ISO timestamp} of the most recent position_stop per ticker.

    Used by the enforcement loop's re-entry hysteresis (G1) — a stop blocks
    re-entry in that ticker for position_stop_reentry_days trading days.
    """
    out: Dict[str, str] = {}
    for row in query_breakers(limit=500):
        if row.get("breaker_type") == "position_stop" and row.get("ticker"):
            out.setdefault(row["ticker"], row["timestamp"])
    return out


# ── Performance log (G2 scoreboard) ─────────────────────────────────────
def upsert_performance(date: str, nav, ret, spy_ret=None, universe_ret=None,
                       contributions=None) -> None:
    """Upsert one daily performance row (idempotent on date). Prod: Postgres."""
    if _use_pg():
        try:
            import common.db
            common.db.upsert_performance(date, nav, ret, spy_ret, universe_ret,
                                         contributions)
        except Exception as exc:  # noqa: BLE001
            log.warning("pg upsert_performance failed (fallback sqlite): %s", exc)
            return _sqlite_upsert_performance(date, nav, ret, spy_ret, universe_ret,
                                              contributions)
        return
    return _sqlite_upsert_performance(date, nav, ret, spy_ret, universe_ret,
                                      contributions)


def _sqlite_upsert_performance(date: str, nav, ret, spy_ret=None, universe_ret=None,
                               contributions=None) -> None:
    if isinstance(contributions, dict):
        contributions = json.dumps(contributions)
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO performance_log
                (date, nav, ret, spy_ret, universe_ret, contributions)
                VALUES (?,?,?,?,?,?)
                """,
                (date, nav, ret, spy_ret, universe_ret, contributions),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("upsert_performance failed: %s", exc)


def query_performance(limit: int = 1000) -> List[Dict]:
    """Most recent performance rows (NEWEST first). Prod: Postgres."""
    if _use_pg():
        try:
            import common.db
            return [dict(r) for r in common.db.query_performance(limit)]
        except Exception as exc:  # noqa: BLE001
            log.warning("pg query_performance failed (fallback sqlite): %s", exc)
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT date, nav, ret, spy_ret, universe_ret, contributions "
                "FROM performance_log ORDER BY date DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out: List[Dict] = []
        for r in rows:
            row = {
                "date": r[0],
                "nav": r[1],
                "ret": r[2],
                "spy_ret": r[3],
                "universe_ret": r[4],
            }
            try:
                row["contributions"] = json.loads(r[5]) if r[5] else {}
            except (ValueError, TypeError):
                row["contributions"] = {}
            out.append(row)
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("query_performance failed: %s", exc)
        return []


# ── Alerts file (G5) ─────────────────────────────────────────────────────
ALERTS_LOG = Path(__file__).resolve().parent / "logs" / "ns6_alerts.log"


def append_alert(event_type: str, detail: str) -> None:
    """Append one line to logs/ns6_alerts.log (G5). Fail-open.

    Written on NEW breaker/stop/crisis-entry fires (never on repeated polls —
    callers dedupe before calling). Kept a plain file this phase; an email
    notifier can tail it later.
    """
    try:
        ALERTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat()
        with open(ALERTS_LOG, "a") as fh:
            fh.write(f"{ts} {event_type} {detail}\n")
    except Exception as exc:  # noqa: BLE001
        log.warning("append_alert failed: %s", exc)


# ── Settings (active profile persistence) ───────────────────────────────
ACTIVE_PROFILE_KEY = "active_profile"
DEFAULT_PROFILE = "balanced"


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    """Read a settings row, or None/default if absent. Prod: Postgres."""
    if _use_pg():
        try:
            import common.db
            return common.db.get_setting(key, default)
        except Exception as exc:  # noqa: BLE001
            log.warning("pg get_setting failed (fallback sqlite): %s", exc)
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
    """Upsert a settings row. Prod: Postgres."""
    if _use_pg():
        try:
            import common.db
            common.db.set_setting(key, value)
        except Exception as exc:  # noqa: BLE001
            log.warning("pg set_setting failed (fallback sqlite): %s", exc)
            return _sqlite_set_setting(key, value)
        return
    return _sqlite_set_setting(key, value)


def _sqlite_set_setting(key: str, value: str) -> None:
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


# ── Fast de-risk crisis state (R3) ─────────────────────────────────────
# The fast-de-risk VIX-smile uses hysteresis: crisis_mode is carried across
# days (enter VIX >= crisis_in, exit VIX <= crisis_out, hold between). It is
# persisted so the daily price feed updates it and the enforcement loop reads
# it — never recomputed inside a read-only GET.
CRISIS_MODE_KEY = "crisis_mode"


def get_crisis_mode() -> bool:
    """Persisted fast-de-risk crisis-mode flag (default False). Fail-open."""
    return get_setting(CRISIS_MODE_KEY, "0") == "1"


def set_crisis_mode(active: bool) -> bool:
    """Persist the fast-de-risk crisis-mode flag. Returns the normalized value."""
    set_setting(CRISIS_MODE_KEY, "1" if active else "0")
    return bool(active)
