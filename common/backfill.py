"""backfill.py — NS-DB Phase 1 backfill (frontier-owned, correctness-critical).

Reads the current fragmented sources and writes them into Postgres. Additive:
the source JSON/SQLite files are NOT deleted (Phase 2/3 rewire readers first).

Backfills:
  portfolios / positions / portfolio_guardrails / portfolio_nav  <- paper_portfolio.json + portfolios.json
  strategy_output   <- sleeve_blend.json, selection.json, signals.json, strategy_alloc.json
  strategy_returns  <- strategy_streams.json
  regime_history    <- common/data/regime_history.db

Run with py3.9 (has psycopg2): env -u PYTHONPATH <py3.9> backfill.py

Idempotent: re-running overwrites the same rows (ON CONFLICT / DELETE-then-insert).
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common.db as db  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent / "Project_Nine_Street"


# ── Portfolio ──────────────────────────────────────────────────────────────
def _backfill_portfolios() -> int:
    """paper_portfolio.json → 'paper' (live); portfolios.json → 'hyperscaler' (policy)."""
    n = 0
    pp = ROOT / "scripts" / "paper_portfolio.json"
    if pp.exists():
        doc = json.loads(pp.read_text())
        if db.write_portfolio("paper", doc, kind="live"):
            n += 1
    pf = ROOT / "NS-5_QA" / "data" / "portfolios.json"
    if pf.exists():
        named = json.loads(pf.read_text())
        for name, holdings in named.items():
            # named portfolios are policy holdings: {ticker: weight-or-shares}.
            # Store as a minimal 'policy' portfolio (no NAV/positions-as-traded).
            doc = {
                "account": {"initial_balance": 100000.0, "cash": 0.0,
                            "total_nav": 100000.0, "commissions_paid": 0.0,
                            "last_updated": None},
                "positions": {"equities": {}, "options": {}},
                "guardrails": {},
                "history": [],
            }
            # preserve the policy definition in a strategy_output row instead
            # (named policy holdings are target weights, not traded positions)
            db.write_strategy_output("ns5", f"policy_{name.lower()}", holdings)
            if db.write_portfolio(name.lower(), doc, kind="policy"):
                n += 1
    return n


# ── Strategy output ────────────────────────────────────────────────────────
_STRATEGY_FILES = [
    ("ns5", "blend", ROOT / "NS-5_QA" / "data" / "sleeve_blend.json"),
    ("ns7", "selection", ROOT / "NS-7_QA" / "data" / "selection.json"),
    ("ns8", "signals", ROOT / "NS-8_QA" / "data" / "signals.json"),
    ("nsx", "alloc", ROOT / "NS-X_QA" / "data" / "strategy_alloc.json"),
]


def _backfill_strategy_output() -> int:
    n = 0
    for service, kind, path in _STRATEGY_FILES:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text())
        except Exception:
            continue
        as_of = payload.get("as_of") if isinstance(payload, dict) else None
        if db.write_strategy_output(service, kind, payload, as_of=as_of):
            n += 1
    return n


# ── Strategy returns ───────────────────────────────────────────────────────
def _backfill_strategy_returns() -> int:
    """strategy_streams.json → strategy_returns (returns are date-agnostic in the
    source; assign synthetic sequential dates starting 2006-01-03 to preserve order)."""
    sp = ROOT / "NS-X_QA" / "data" / "strategy_streams.json"
    if not sp.exists():
        return 0
    from datetime import date, timedelta

    doc = json.loads(sp.read_text())
    streams = doc.get("streams", {})
    n = 0
    for sid, s in streams.items():
        returns = s.get("returns", [])
        if not returns:
            continue
        start = date(2006, 1, 3)
        rows = [
            {"date": (start + timedelta(days=i)).isoformat(),
             "return": float(r), "source": s.get("source", "")}
            for i, r in enumerate(returns)
        ]
        if db.write_strategy_returns(sid, rows):
            n += 1
    return n


# ── Regime history ─────────────────────────────────────────────────────────
def _backfill_regime() -> int:
    regime_db = Path(__file__).resolve().parent.parent / "common" / "data" / "regime_history.db"
    if not regime_db.exists():
        return 0
    try:
        c = sqlite3.connect(regime_db)
        rows = c.execute("SELECT * FROM regime_history ORDER BY date").fetchall()
        cols = [x[1] for x in c.execute("PRAGMA table_info(regime_history)").fetchall()]
        c.close()
    except Exception:
        return 0

    conn = db._connect()
    if conn is None:
        return 0
    n = 0
    try:
        with conn, conn.cursor() as cur:
            for r in rows:
                d = dict(zip(cols, r))
                cur.execute(
                    "INSERT INTO regime_history (date, regime, confidence, flags, "
                    " cpi_yoy, gdp_qoq, unrate, curve_bp, baa_aaa_bp, nfci, vix, "
                    " corr, wti, recorded_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (date) DO UPDATE SET regime=EXCLUDED.regime, "
                    " confidence=EXCLUDED.confidence, flags=EXCLUDED.flags, "
                    " cpi_yoy=EXCLUDED.cpi_yoy, gdp_qoq=EXCLUDED.gdp_qoq, "
                    " unrate=EXCLUDED.unrate, curve_bp=EXCLUDED.curve_bp, "
                    " baa_aaa_bp=EXCLUDED.baa_aaa_bp, nfci=EXCLUDED.nfci, "
                    " vix=EXCLUDED.vix, corr=EXCLUDED.corr, wti=EXCLUDED.wti, "
                    " recorded_at=EXCLUDED.recorded_at",
                    (d.get("date"), d.get("regime"), d.get("confidence"), d.get("flags"),
                     d.get("cpi_yoy"), d.get("gdp_qoq"), d.get("unrate"), d.get("curve_bp"),
                     d.get("baa_aaa_bp"), d.get("nfci"), d.get("vix"), d.get("corr"),
                     d.get("wti"), d.get("recorded_at")),
                )
                n += 1
        return n
    except Exception:
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main() -> int:
    if not db.available():
        print("ERROR: Postgres unavailable (psycopg2 missing or DB down). Aborting.")
        return 1
    db.ensure_schema()
    p = _backfill_portfolios()
    so = _backfill_strategy_output()
    sr = _backfill_strategy_returns()
    rg = _backfill_regime()
    print(f"backfilled: portfolios={p} strategy_output={so} "
          f"strategy_returns={sr} regime_history={rg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
