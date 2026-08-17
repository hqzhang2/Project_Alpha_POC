"""common/db.py — centralized strategy data store (PostgreSQL, project_alpha).

The single seam every NS service uses to read/write shared state. Replaces the
scattered per-service SQLite DBs + JSON files (paper_portfolio.json,
signals.json, selection.json, sleeve_blend.json, strategy_alloc.json,
strategy_streams.json, regime_history.db, ns6/7/8.db).

Postgres DSN comes from env DATABASE_URL, falling back to the local DSN used by
A_T (financials.py). Fail-open everywhere: a down DB returns empty/None, never
raises — matching the regime_store.py pattern so no service crashes on a cold
or unreachable Postgres.

FRONTIER-OWNED: schema + semantics. Junior owns call-site wiring, not this file.
"""
from __future__ import annotations

import datetime
import json
import os
from typing import Any, Dict, List, Optional

try:
    import psycopg2
    import psycopg2.extras
    _HAS_PSYCOPG2 = True
except ImportError:  # fail-open if the runtime lacks psycopg2
    psycopg2 = None  # type: ignore[assignment]
    _HAS_PSYCOPG2 = False

DSN = os.environ.get(
    "DATABASE_URL",
    "dbname=project_alpha user=chuck host=localhost",
)

_SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")


def _connect():
    """Return a psycopg2 connection, or None if unavailable."""
    if not _HAS_PSYCOPG2:
        return None
    try:
        return psycopg2.connect(DSN)
    except Exception:
        return None


def available() -> bool:
    """True if psycopg2 is importable AND Postgres is reachable."""
    conn = _connect()
    if conn is None:
        return False
    try:
        conn.close()
        return True
    except Exception:
        return False


def ensure_schema() -> bool:
    """Create tables if missing (idempotent). Returns True on success."""
    conn = _connect()
    if conn is None:
        return False
    try:
        with open(_SCHEMA_PATH) as f:
            ddl = f.read()
        with conn, conn.cursor() as cur:
            cur.execute(ddl)
        return True
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── Portfolio ──────────────────────────────────────────────────────────────
def get_portfolio(name: str) -> Optional[Dict[str, Any]]:
    """Read a portfolio doc (replaces reading paper_portfolio.json).

    Returns {'account': {...}, 'positions': {'equities': {...}, 'options': {}},
             'guardrails': {...}, 'history': [...]} or None on failure.
    """
    conn = _connect()
    if conn is None:
        return None
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM portfolios WHERE name = %s", (name,)
            )
            pf = cur.fetchone()
            if pf is None:
                return None
            cur.execute(
                "SELECT * FROM positions WHERE portfolio_id = %s "
                "ORDER BY ticker", (pf["id"],)
            )
            positions = cur.fetchall()
            cur.execute(
                "SELECT * FROM portfolio_guardrails WHERE portfolio_id = %s "
                "ORDER BY as_of DESC LIMIT 1", (pf["id"],)
            )
            guard = cur.fetchone()
            cur.execute(
                "SELECT date, nav, note FROM portfolio_nav WHERE portfolio_id = %s "
                "ORDER BY date", (pf["id"],)
            )
            nav = cur.fetchall()

        equities = {
            p["ticker"]: {
                "shares": float(p["shares"]),
                "entry_price": float(p["entry_price"]),
                "current_price": float(p["current_price"]) if p["current_price"] is not None else None,
                "allocation_pct": float(p["allocation_pct"]) if p["allocation_pct"] is not None else None,
                "strategy": p["strategy"],
                "pnl": float(p["pnl"] or 0.0),
                "pnl_pct": float(p["pnl_pct"] or 0.0),
            }
            for p in positions
        }
        account = {
            "initial_balance": float(pf["initial_balance"]),
            "cash": float(pf["cash"]),
            "total_nav": float(pf["total_nav"]),
            "commissions_paid": float(pf["commissions"]),
            "last_updated": pf["updated_at"].strftime("%Y-%m-%d") if pf["updated_at"] else None,
        }
        guardrails = {k: (float(v) if v is not None else v) for k, v in guard.items()
                      if k in ("n", "eff_n", "max_weight", "weights_sum",
                               "min_eff_n", "max_name_w")} if guard else {}
        history = [
            {"date": n["date"].strftime("%Y-%m-%d"), "nav": float(n["nav"]),
             "note": n["note"]}
            for n in nav
        ]
        return {"account": account, "positions": {"equities": equities, "options": {}},
                "guardrails": guardrails, "history": history}
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def write_portfolio(name: str, doc: Dict[str, Any], kind: str = "live") -> bool:
    """Write a portfolio doc (replaces writing paper_portfolio.json). Returns True.

    doc = {'account': {...}, 'positions': {'equities': {ticker: {...}}},
           'guardrails': {...}, 'history': [...]} — the shape NS-PC's
    constructor currently produces and NS-1/NS-6 read.
    """
    conn = _connect()
    if conn is None:
        return False
    try:
        acct = doc.get("account", {})
        guard = doc.get("guardrails", {})
        equities = doc.get("positions", {}).get("equities", {})
        as_of = acct.get("last_updated") or datetime.date.today().isoformat()

        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO portfolios (name, kind, initial_balance, cash, "
                " total_nav, commissions, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s, now()) "
                "ON CONFLICT (name) DO UPDATE SET "
                " kind=EXCLUDED.kind, initial_balance=EXCLUDED.initial_balance, "
                " cash=EXCLUDED.cash, total_nav=EXCLUDED.total_nav, "
                " commissions=EXCLUDED.commissions, updated_at=now() "
                "RETURNING id",
                (name, kind, acct.get("initial_balance", 0.0),
                 acct.get("cash", 0.0), acct.get("total_nav", 0.0),
                 acct.get("commissions_paid", 0.0)),
            )
            pf_id = cur.fetchone()["id"]

            # replace positions for this as_of (idempotent re-write of the book)
            cur.execute("DELETE FROM positions WHERE portfolio_id=%s AND as_of=%s",
                        (pf_id, as_of))
            for ticker, p in equities.items():
                cur.execute(
                    "INSERT INTO positions (portfolio_id, ticker, shares, entry_price, "
                    " current_price, allocation_pct, strategy, pnl, pnl_pct, as_of) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (pf_id, ticker, p.get("shares", 0.0), p.get("entry_price", 0.0),
                     p.get("current_price"), p.get("allocation_pct"),
                     p.get("strategy"), p.get("pnl", 0.0), p.get("pnl_pct", 0.0),
                     as_of),
                )

            if guard:
                cur.execute(
                    "INSERT INTO portfolio_guardrails (portfolio_id, as_of, n, eff_n, "
                    " max_weight, weights_sum, min_eff_n, max_name_w) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (portfolio_id, as_of) DO UPDATE SET "
                    " n=EXCLUDED.n, eff_n=EXCLUDED.eff_n, max_weight=EXCLUDED.max_weight, "
                    " weights_sum=EXCLUDED.weights_sum, min_eff_n=EXCLUDED.min_eff_n, "
                    " max_name_w=EXCLUDED.max_name_w",
                    (pf_id, as_of, guard.get("n"), guard.get("eff_n"),
                     guard.get("max_weight"), guard.get("weights_sum"),
                     guard.get("min_eff_n"), guard.get("max_name_w")),
                )

            for h in doc.get("history", []):
                cur.execute(
                    "INSERT INTO portfolio_nav (portfolio_id, date, nav, note) "
                    "VALUES (%s,%s,%s,%s) ON CONFLICT (portfolio_id, date) DO UPDATE "
                    "SET nav=EXCLUDED.nav, note=EXCLUDED.note",
                    (pf_id, h.get("date"), h.get("nav", 0.0), h.get("note")),
                )
        return True
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── Strategy output ────────────────────────────────────────────────────────
def latest_strategy_output(service: str, kind: str) -> Optional[Dict[str, Any]]:
    """Latest payload for a (service, kind) — replaces reading signals/selection/
    blend/alloc JSON. Returns the payload dict, or None."""
    conn = _connect()
    if conn is None:
        return None
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM strategy_output WHERE service=%s AND kind=%s "
                "ORDER BY as_of DESC LIMIT 1", (service, kind)
            )
            row = cur.fetchone()
        return row[0] if row else None
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def write_strategy_output(service: str, kind: str, payload: Dict[str, Any],
                          as_of: Optional[str] = None) -> bool:
    """Write a strategy output payload. as_of defaults to today."""
    conn = _connect()
    if conn is None:
        return False
    try:
        as_of = as_of or datetime.date.today().isoformat()
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO strategy_output (service, kind, as_of, payload) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT (service, kind, as_of) DO UPDATE "
                "SET payload=EXCLUDED.payload, generated_at=now()",
                (service, kind, as_of, json.dumps(payload)),
            )
        return True
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── Strategy returns ───────────────────────────────────────────────────────
def strategy_returns(strategy_id: str) -> List[float]:
    """The daily return stream for a strategy (replaces strategy_streams.json).
    Fail-open: [] on missing/error."""
    conn = _connect()
    if conn is None:
        return []
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT return FROM strategy_returns WHERE strategy_id=%s "
                "ORDER BY date", (strategy_id,)
            )
            return [float(r[0]) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def write_strategy_returns(strategy_id: str, rows: List[Dict[str, Any]]) -> bool:
    """Write a strategy return stream. rows = [{'date', 'return', 'source'}, ...].
    Replaces rows for this strategy (idempotent)."""
    conn = _connect()
    if conn is None:
        return False
    try:
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM strategy_returns WHERE strategy_id=%s", (strategy_id,))
            for r in rows:
                cur.execute(
                    "INSERT INTO strategy_returns (strategy_id, date, return, source) "
                    "VALUES (%s,%s,%s,%s)",
                    (strategy_id, r.get("date"), r.get("return"), r.get("source")),
                )
        return True
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def append_nav(portfolio: str, date: str, nav: float, note: str = "") -> bool:
    """Append a NAV point to a portfolio's history (replaces history array push)."""
    conn = _connect()
    if conn is None:
        return False
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO portfolio_nav (portfolio_id, date, nav, note) "
                "SELECT id, %s, %s, %s FROM portfolios WHERE name=%s "
                "ON CONFLICT (portfolio_id, date) DO UPDATE SET nav=EXCLUDED.nav, "
                "note=EXCLUDED.note",
                (date, nav, note, portfolio),
            )
        return True
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── NS-6 enforcement logs (mirrors NS-6_QA/store.py API) ──────────────────
def upsert_drawdown(date: str, spy_dd, portfolio_dd, budget, remaining,
                    multiplier, vix_level=None, position_drawdowns=None,
                    cross_sectional_corr=None) -> bool:
    """Upsert one drawdown snapshot (idempotent on date). Mirrors store.py."""
    conn = _connect()
    if conn is None:
        return False
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO drawdown_log (date, spy_dd_pct, portfolio_dd_pct, "
                " budget_pct, budget_remaining_pct, multiplier, vix_level, "
                " position_drawdowns, cross_sectional_corr) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (date) DO UPDATE SET "
                " spy_dd_pct=EXCLUDED.spy_dd_pct, portfolio_dd_pct=EXCLUDED.portfolio_dd_pct, "
                " budget_pct=EXCLUDED.budget_pct, budget_remaining_pct=EXCLUDED.budget_remaining_pct, "
                " multiplier=EXCLUDED.multiplier, vix_level=EXCLUDED.vix_level, "
                " position_drawdowns=EXCLUDED.position_drawdowns, "
                " cross_sectional_corr=EXCLUDED.cross_sectional_corr",
                (date, spy_dd, portfolio_dd, budget, remaining, multiplier,
                 vix_level, _jsonb(position_drawdowns), cross_sectional_corr),
            )
        return True
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def latest_drawdown() -> Optional[Dict[str, Any]]:
    """Most recent drawdown row (dict) or None. Mirrors store.latest()."""
    conn = _connect()
    if conn is None:
        return None
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM drawdown_log ORDER BY date DESC LIMIT 1")
            row = cur.fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def query_drawdown(days: int = 30) -> List[Dict[str, Any]]:
    """Last N drawdown rows, newest first. Mirrors store.query_window()."""
    conn = _connect()
    if conn is None:
        return []
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM drawdown_log ORDER BY date DESC LIMIT %s", (days,))
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def upsert_performance(date: str, nav, ret, spy_ret=None, universe_ret=None,
                       contributions=None) -> bool:
    """Upsert one daily performance row. Mirrors store.upsert_performance()."""
    conn = _connect()
    if conn is None:
        return False
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO performance_log (date, nav, ret, spy_ret, "
                " universe_ret, contributions) VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (date) DO UPDATE SET nav=EXCLUDED.nav, ret=EXCLUDED.ret, "
                " spy_ret=EXCLUDED.spy_ret, universe_ret=EXCLUDED.universe_ret, "
                " contributions=EXCLUDED.contributions",
                (date, nav, ret, spy_ret, universe_ret, _jsonb(contributions)),
            )
        return True
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def query_performance(limit: int = 1000) -> List[Dict[str, Any]]:
    """Most recent performance rows (newest first). Mirrors store.query_performance()."""
    conn = _connect()
    if conn is None:
        return []
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT date, nav, ret, spy_ret, universe_ret, contributions "
                "FROM performance_log ORDER BY date DESC LIMIT %s", (limit,))
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def log_circuit_breaker(breaker_type: str, ticker: Optional[str], detail: str) -> bool:
    """Append a circuit-breaker event. Mirrors store.log_circuit_breaker()."""
    conn = _connect()
    if conn is None:
        return False
    try:
        import datetime as _dt
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO circuit_breaker_log (timestamp, breaker_type, ticker, detail) "
                "VALUES (%s,%s,%s,%s)",
                (_dt.datetime.now().isoformat(), breaker_type, ticker, detail),
            )
        return True
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def query_breakers(limit: int = 50) -> List[Dict[str, Any]]:
    """Most recent circuit-breaker events (newest first). Mirrors store.query_breakers()."""
    conn = _connect()
    if conn is None:
        return []
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM circuit_breaker_log ORDER BY id DESC LIMIT %s", (limit,))
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    """Read a settings row. Mirrors store.get_setting()."""
    conn = _connect()
    if conn is None:
        return default
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT value FROM settings WHERE key=%s", (key,))
            row = cur.fetchone()
        return row[0] if row else default
    except Exception:
        return default
    finally:
        try:
            conn.close()
        except Exception:
            pass


def set_setting(key: str, value: str) -> bool:
    """Upsert a settings row. Mirrors store.set_setting()."""
    conn = _connect()
    if conn is None:
        return False
    try:
        with conn, conn.cursor() as cur:
            cur.execute("INSERT INTO settings (key, value) VALUES (%s,%s) "
                        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                        (key, value))
        return True
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _jsonb(v: Any) -> Any:
    """Coerce a value to a JSONB-safe form (dict → json string; None stays None)."""
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return v


# ── NS-7 league / volume / selection / meta (mirrors NS-7_QA/store.py) ────
def upsert_league(ticker: str, league: str, consecutive_compliant: int,
                  consecutive_noncompliant: int, first_seen: str, last_seen: str) -> bool:
    conn = _connect()
    if conn is None:
        return False
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ns7_league (ticker, league, consecutive_compliant, "
                " consecutive_noncompliant, first_seen, last_seen) "
                "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (ticker) DO UPDATE SET "
                " league=EXCLUDED.league, consecutive_compliant=EXCLUDED.consecutive_compliant, "
                " consecutive_noncompliant=EXCLUDED.consecutive_noncompliant, "
                " first_seen=EXCLUDED.first_seen, last_seen=EXCLUDED.last_seen",
                (ticker.upper(), league, consecutive_compliant,
                 consecutive_noncompliant, first_seen, last_seen))
        return True
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_league(ticker: str) -> Optional[Dict[str, Any]]:
    conn = _connect()
    if conn is None:
        return None
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM ns7_league WHERE ticker=%s", (ticker.upper(),))
            row = cur.fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def league_counts() -> Dict[str, int]:
    conn = _connect()
    if conn is None:
        return {}
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT league, COUNT(*) FROM ns7_league GROUP BY league")
            return {r[0]: r[1] for r in cur.fetchall()}
    except Exception:
        return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def all_leagues() -> List[Dict[str, Any]]:
    conn = _connect()
    if conn is None:
        return []
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM ns7_league ORDER BY ticker")
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def upsert_volume_many(rows: List[tuple]) -> int:
    if not rows:
        return 0
    conn = _connect()
    if conn is None:
        return 0
    try:
        with conn, conn.cursor() as cur:
            for t, d, v in rows:
                cur.execute(
                    "INSERT INTO ns7_volume (ticker, date, volume) VALUES (%s,%s,%s) "
                    "ON CONFLICT (ticker, date) DO UPDATE SET volume=EXCLUDED.volume",
                    (t.upper(), d, float(v)))
        return len(rows)
    except Exception:
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def volume_series(ticker: str, start: str, end: str) -> List[tuple]:
    conn = _connect()
    if conn is None:
        return []
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT date, volume FROM ns7_volume WHERE ticker=%s AND date BETWEEN %s AND %s "
                "ORDER BY date", (ticker.upper(), start, end))
            return [(r[0], r[1]) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def avg_daily_volume(ticker: str, as_of: str, window_days: int) -> Optional[float]:
    conn = _connect()
    if conn is None:
        return None
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT AVG(volume) FROM ("
                "  SELECT volume FROM ns7_volume WHERE ticker=%s AND date <= %s "
                "  ORDER BY date DESC LIMIT %s) sub",
                (ticker.upper(), as_of, window_days))
            row = cur.fetchone()
        return float(row[0]) if row and row[0] is not None else None
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def volume_coverage(ticker: str) -> tuple:
    conn = _connect()
    if conn is None:
        return (None, None, 0)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM ns7_volume WHERE ticker=%s",
                        (ticker.upper(),))
            row = cur.fetchone()
        return (row[0], row[1], row[2]) if row else (None, None, 0)
    except Exception:
        return (None, None, 0)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def save_selection(as_of: str, payload: dict) -> int:
    conn = _connect()
    if conn is None:
        return 0
    try:
        import datetime as _dt
        gen = _dt.datetime.now(_dt.timezone.utc).isoformat()
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ns7_selection (generated_at, as_of, payload) VALUES (%s,%s,%s) RETURNING id",
                (gen, as_of, _jsonb(payload)))
            row = cur.fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def latest_selection() -> Optional[Dict[str, Any]]:
    conn = _connect()
    if conn is None:
        return None
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT generated_at, as_of, payload FROM ns7_selection ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["payload"] = d.get("payload") or {}
        return d
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def set_meta(key: str, value: str) -> bool:
    conn = _connect()
    if conn is None:
        return False
    try:
        with conn, conn.cursor() as cur:
            cur.execute("INSERT INTO ns7_refresh_meta (key, value) VALUES (%s,%s) "
                        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (key, value))
        return True
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_meta(key: str) -> Optional[str]:
    conn = _connect()
    if conn is None:
        return None
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT value FROM ns7_refresh_meta WHERE key=%s", (key,))
            row = cur.fetchone()
        return row[0] if row else None
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── NS-8 signals / tranche / audit (mirrors NS-8_QA/store.py) ─────────────
def upsert_signal(as_of: str, signals: Dict[str, int], weights: Dict[str, float],
                  version: int, generated_at: str) -> bool:
    conn = _connect()
    if conn is None:
        return False
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ns8_signals (as_of, signals_json, weights_json, version, generated_at) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (as_of) DO UPDATE SET "
                " signals_json=EXCLUDED.signals_json, weights_json=EXCLUDED.weights_json, "
                " version=EXCLUDED.version, generated_at=EXCLUDED.generated_at",
                (as_of, _jsonb(signals), _jsonb(weights), version, generated_at))
        return True
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_latest_signal() -> Optional[Dict[str, Any]]:
    conn = _connect()
    if conn is None:
        return None
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM ns8_signals ORDER BY as_of DESC LIMIT 1")
            row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["signals"] = d.get("signals_json") or {}
        d["weights"] = d.get("weights_json") or {}
        d.pop("signals_json", None)
        d.pop("weights_json", None)
        return d
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_signal(as_of: str) -> Optional[Dict[str, Any]]:
    conn = _connect()
    if conn is None:
        return None
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM ns8_signals WHERE as_of=%s", (as_of,))
            row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["signals"] = d.get("signals_json") or {}
        d["weights"] = d.get("weights_json") or {}
        d.pop("signals_json", None)
        d.pop("weights_json", None)
        return d
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def init_tranche_state() -> bool:
    conn = _connect()
    if conn is None:
        return False
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM ns8_tranche_state")
            if cur.fetchone()[0] >= 4:
                return True
            cur.execute("DELETE FROM ns8_tranche_state")
            for i in range(4):
                cur.execute("INSERT INTO ns8_tranche_state (tranche_idx, next_rebalance, last_rebalance) "
                            "VALUES (%s, NULL, NULL)", (i,))
        return True
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_tranche_state() -> List[Dict[str, Any]]:
    conn = _connect()
    if conn is None:
        return []
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM ns8_tranche_state ORDER BY tranche_idx")
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def update_tranche_rebalance(tranche_idx: int, next_rebalance: str, last_rebalance: str) -> bool:
    conn = _connect()
    if conn is None:
        return False
    try:
        with conn, conn.cursor() as cur:
            cur.execute("UPDATE ns8_tranche_state SET next_rebalance=%s, last_rebalance=%s "
                        "WHERE tranche_idx=%s", (next_rebalance, last_rebalance, tranche_idx))
        return True
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def log_audit(tranche_idx: int, symbol: str, side: str, qty: float,
              order_id: Optional[str] = None) -> bool:
    conn = _connect()
    if conn is None:
        return False
    try:
        import datetime as _dt
        ts = _dt.datetime.now().isoformat(timespec="seconds")
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ns8_audit_log (timestamp, tranche_idx, symbol, side, qty, order_id) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (ts, tranche_idx, symbol, side, qty, order_id))
        return True
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_audit_log(limit: int = 100) -> List[Dict[str, Any]]:
    conn = _connect()
    if conn is None:
        return []
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM ns8_audit_log ORDER BY id DESC LIMIT %s", (limit,))
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── Regime history (mirrors common/regime_store.py) ───────────────────────
def upsert_regime(date: str, row: Dict[str, Any]) -> bool:
    conn = _connect()
    if conn is None:
        return False
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO regime_history (date, regime, confidence, flags, "
                " cpi_yoy, gdp_qoq, unrate, curve_bp, baa_aaa_bp, nfci, vix, corr, wti, recorded_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (date) DO UPDATE SET regime=EXCLUDED.regime, "
                " confidence=EXCLUDED.confidence, flags=EXCLUDED.flags, "
                " cpi_yoy=EXCLUDED.cpi_yoy, gdp_qoq=EXCLUDED.gdp_qoq, "
                " unrate=EXCLUDED.unrate, curve_bp=EXCLUDED.curve_bp, "
                " baa_aaa_bp=EXCLUDED.baa_aaa_bp, nfci=EXCLUDED.nfci, "
                " vix=EXCLUDED.vix, corr=EXCLUDED.corr, wti=EXCLUDED.wti, "
                " recorded_at=EXCLUDED.recorded_at",
                (date, row.get("regime"), row.get("confidence"), row.get("flags"),
                 row.get("cpi_yoy"), row.get("gdp_qoq"), row.get("unrate"), row.get("curve_bp"),
                 row.get("baa_aaa_bp"), row.get("nfci"), row.get("vix"), row.get("corr"),
                 row.get("wti"), row.get("recorded_at")))
        return True
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def latest_regime() -> Optional[Dict[str, Any]]:
    conn = _connect()
    if conn is None:
        return None
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM regime_history ORDER BY date DESC LIMIT 1")
            row = cur.fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def query_regime_window(days: int = 750) -> List[Dict[str, Any]]:
    conn = _connect()
    if conn is None:
        return []
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM regime_history ORDER BY date DESC LIMIT %s", (days,))
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass
