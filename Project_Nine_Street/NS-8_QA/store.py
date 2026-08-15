"""store.py — NS-8 SQLite Persistence Layer.

Handles signals, tranche state, and audit logging.
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import config


def _conn() -> sqlite3.Connection:
    """Get a database connection with row factory."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initialize all tables."""
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
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


# ── Export ──────────────────────────────────────────────────────────────

def export_signals_json() -> None:
    """Export latest signal to JSON file for NS-5 consumption."""
    signal = get_latest_signal()
    if signal:
        config.SIGNALS_PATH.write_text(json.dumps(signal, indent=2, default=str))