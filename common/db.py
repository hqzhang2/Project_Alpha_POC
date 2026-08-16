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
