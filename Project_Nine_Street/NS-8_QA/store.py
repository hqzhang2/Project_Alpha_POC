"""store.py — NS-8 SQLite Persistence Layer.

Handles signals, tranche state, and audit logging. Prod: delegates to
PostgreSQL (common.db); sqlite retained as the fail-open fallback.
"""
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import config

DEFAULT_DB_PATH = str(config.DB_PATH)           # snapshot of the prod path

# Repo root so `import common.db` resolves (this service runs with NS-8_QA/ cwd).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _use_pg() -> bool:
    """True when config.DB_PATH is the prod default (→ PostgreSQL common.db).

    NS-8 tests don't monkeypatch DB_PATH (pure-function tests + a /tranches
    endpoint); in prod config.DB_PATH is the default, so this is True → Postgres.
    Fail-open: pg error → sqlite fallback so NS-8 never loses signal/tranche state.
    """
    return str(config.DB_PATH) == DEFAULT_DB_PATH


def _conn() -> sqlite3.Connection:
    """Get a database connection with row factory."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initialize all tables. Prod: ensure Postgres schema."""
    if _use_pg():
        try:
            import common.db
            common.db.ensure_schema()
        except Exception:
            pass  # fail-open
        return
    conn = _conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                as_of TEXT PRIMARY KEY,
                signals_json TEXT NOT NULL,
                weights_json TEXT NOT NULL,
                version INTEGER NOT NULL,
                generated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tranche_state (
                tranche_idx INTEGER PRIMARY KEY,
                next_rebalance TEXT,
                last_rebalance TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                tranche_idx INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                qty REAL NOT NULL,
                order_id TEXT
            )
        """)
        conn.commit()
    finally:
        conn.close()


# ── Signals ──────────────────────────────────────────────────────────────

def upsert_signal(
    as_of: str,
    signals: Dict[str, int],
    weights: Dict[str, float],
    version: int,
    generated_at: str
) -> None:
    """Insert or replace a monthly signal record."""
    if _use_pg():
        try:
            import common.db
            common.db.upsert_signal(as_of, signals, weights, version, generated_at)
        except Exception:
            pass  # fall through to sqlite
        return
    conn = _conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO signals VALUES (?, ?, ?, ?, ?)",
            (as_of, json.dumps(signals), json.dumps(weights), version, generated_at)
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_signal() -> Optional[Dict[str, Any]]:
    """Get the most recent signal record."""
    if _use_pg():
        try:
            import common.db
            return common.db.get_latest_signal()
        except Exception:
            pass  # fall through to sqlite
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM signals ORDER BY as_of DESC LIMIT 1"
        ).fetchone()
        if row:
            return {
                "as_of": row["as_of"],
                "signals": json.loads(row["signals_json"]),
                "weights": json.loads(row["weights_json"]),
                "version": row["version"],
                "generated_at": row["generated_at"]
            }
        return None
    finally:
        conn.close()


def get_signal(as_of: str) -> Optional[Dict[str, Any]]:
    """Get signal for a specific date."""
    if _use_pg():
        try:
            import common.db
            return common.db.get_signal(as_of)
        except Exception:
            pass  # fall through to sqlite
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM signals WHERE as_of = ?", (as_of,)
        ).fetchone()
        if row:
            return {
                "as_of": row["as_of"],
                "signals": json.loads(row["signals_json"]),
                "weights": json.loads(row["weights_json"]),
                "version": row["version"],
                "generated_at": row["generated_at"]
            }
        return None
    finally:
        conn.close()


# ── Tranche State ────────────────────────────────────────────────────────

def init_tranche_state() -> None:
    """Initialize 4 tranches with staggered rebalance weeks."""
    if _use_pg():
        try:
            import common.db
            common.db.init_tranche_state()
        except Exception:
            pass  # fall through to sqlite
        return
    conn = _conn()
    try:
        # Check if already initialized
        count = conn.execute("SELECT COUNT(*) FROM tranche_state").fetchone()[0]
        if count >= 4:
            return

        conn.execute("DELETE FROM tranche_state")
        for i in range(4):
            conn.execute(
                "INSERT INTO tranche_state (tranche_idx, next_rebalance, last_rebalance) VALUES (?, ?, ?)",
                (i, None, None)
            )
        conn.commit()
    finally:
        conn.close()


def get_tranche_state() -> List[Dict[str, Any]]:
    """Get all tranche states."""
    if _use_pg():
        try:
            import common.db
            return common.db.get_tranche_state()
        except Exception:
            pass  # fall through to sqlite
    conn = _conn()
    try:
        rows = conn.execute("SELECT * FROM tranche_state ORDER BY tranche_idx").fetchall()
        return [
            {
                "tranche_idx": row["tranche_idx"],
                "next_rebalance": row["next_rebalance"],
                "last_rebalance": row["last_rebalance"]
            }
            for row in rows
        ]
    finally:
        conn.close()


def update_tranche_rebalance(tranche_idx: int, next_rebalance: str, last_rebalance: str) -> None:
    """Update a tranche's rebalance dates."""
    if _use_pg():
        try:
            import common.db
            common.db.update_tranche_rebalance(tranche_idx, next_rebalance, last_rebalance)
        except Exception:
            pass  # fall through to sqlite
        return
    conn = _conn()
    try:
        conn.execute(
            "UPDATE tranche_state SET next_rebalance = ?, last_rebalance = ? WHERE tranche_idx = ?",
            (next_rebalance, last_rebalance, tranche_idx)
        )
        conn.commit()
    finally:
        conn.close()


def get_current_tranche(as_of: Optional[str] = None) -> int:
    """Determine which tranche should rebalance this week.
    
    Tranche 0 = week 1, Tranche 1 = week 2, etc.
    """
    if as_of is None:
        as_of = datetime.now().strftime("%Y-%m-%d")
    dt = datetime.strptime(as_of, "%Y-%m-%d")
    # Week of month: 1-4 (approximate)
    week_of_month = (dt.day - 1) // 7
    return week_of_month % 4


# ── Audit Log ────────────────────────────────────────────────────────────

def log_audit(
    tranche_idx: int,
    symbol: str,
    side: str,
    qty: float,
    order_id: Optional[str] = None
) -> None:
    """Log an execution order."""
    if _use_pg():
        try:
            import common.db
            common.db.log_audit(tranche_idx, symbol, side, qty, order_id)
        except Exception:
            pass  # fall through to sqlite
        return
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO audit_log (timestamp, tranche_idx, symbol, side, qty, order_id) VALUES (?, ?, ?, ?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), tranche_idx, symbol, side, qty, order_id)
        )
        conn.commit()
    finally:
        conn.close()


def get_audit_log(limit: int = 100) -> List[Dict[str, Any]]:
    """Get recent audit log entries."""
    if _use_pg():
        try:
            import common.db
            return common.db.get_audit_log(limit)
        except Exception:
            pass  # fall through to sqlite
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


# ── Export ──────────────────────────────────────────────────────────────

def export_signals_json(doc: Optional[Dict[str, Any]] = None) -> None:
    """Export the signal document to JSON file for NS-5 consumption.

    v4.7: `doc` is the ENRICHED document from pipeline (feed-contract metadata
    — service/strategy/method/guardrails/eff_n/gross_risk_exposure). When not
    supplied, falls back to the latest DB row (legacy shape, no metadata).

    NS-DB: also write the full document to `strategy_output` (service=ns8,
    kind=signals) so cross-service consumers read from Postgres, not the JSON
    file. The file write is kept as a backward-compat artifact. Fail-open.
    """
    if doc is None:
        doc = get_latest_signal()
    if not doc:
        return
    try:
        config.SIGNALS_PATH.write_text(json.dumps(doc, indent=2, default=str))
    except Exception:
        pass  # fail-open
    try:
        import common.db
        as_of = doc.get("as_of")
        common.db.write_strategy_output("ns8", "signals", doc, as_of=as_of)
    except Exception:
        pass  # fail-open
