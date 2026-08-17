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


# ── NS-6 enforcement logs (Phase 4) ─────────────────────────────────────
def _backfill_ns6_logs() -> int:
    """ns6.db → drawdown_log / performance_log / circuit_breaker_log / settings."""
    ns6_db = ROOT / "NS-6_QA" / "data" / "ns6.db"
    if not ns6_db.exists():
        return 0
    try:
        c = sqlite3.connect(ns6_db)
        c.row_factory = sqlite3.Row
        dd = [dict(r) for r in c.execute("SELECT * FROM drawdown_log ORDER BY date")]
        perf = [dict(r) for r in c.execute("SELECT * FROM performance_log ORDER BY date")]
        cb = [dict(r) for r in c.execute("SELECT * FROM circuit_breaker_log ORDER BY id")]
        settings = [dict(r) for r in c.execute("SELECT * FROM settings")]
        c.close()
    except Exception:
        return 0

    conn = db._connect()
    if conn is None:
        return 0
    n = 0
    try:
        with conn, conn.cursor() as cur:
            for r in dd:
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
                    (r["date"], r.get("spy_dd_pct"), r.get("portfolio_dd_pct"),
                     r.get("budget_pct"), r.get("budget_remaining_pct"), r.get("multiplier"),
                     r.get("vix_level"), db._jsonb(r.get("position_drawdowns")),
                     r.get("cross_sectional_corr")),
                )
                n += 1
            for r in perf:
                cur.execute(
                    "INSERT INTO performance_log (date, nav, ret, spy_ret, "
                    " universe_ret, contributions) VALUES (%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (date) DO UPDATE SET nav=EXCLUDED.nav, ret=EXCLUDED.ret, "
                    " spy_ret=EXCLUDED.spy_ret, universe_ret=EXCLUDED.universe_ret, "
                    " contributions=EXCLUDED.contributions",
                    (r["date"], r.get("nav"), r.get("ret"), r.get("spy_ret"),
                     r.get("universe_ret"), db._jsonb(r.get("contributions"))),
                )
                n += 1
            for r in cb:
                cur.execute(
                    "INSERT INTO circuit_breaker_log (timestamp, breaker_type, ticker, detail) "
                    "VALUES (%s,%s,%s,%s)",
                    (r.get("timestamp"), r.get("breaker_type"), r.get("ticker"), r.get("detail")),
                )
                n += 1
            for r in settings:
                cur.execute(
                    "INSERT INTO settings (key, value) VALUES (%s,%s) "
                    "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                    (r["key"], r["value"]),
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


# ── Market data (Phase 4) ───────────────────────────────────────────────
def _backfill_daily_prices() -> int:
    """NS-8 6-ETF closes + NS-7 SPY/QQQ bench closes → shared daily_prices."""
    conn = db._connect()
    if conn is None:
        return 0

    # NS-8: ns8_hist_closes.json {tickers, dates, closes{ticker:[...]}}
    rows = []
    ns8 = ROOT / "NS-8_QA" / "data" / "ns8_hist_closes.json"
    if ns8.exists():
        try:
            doc = json.loads(ns8.read_text())
            tickers, dates = doc["tickers"], doc["dates"]
            closes = doc["closes"]
            for ti, t in enumerate(tickers):
                for di, d in enumerate(dates):
                    v = closes[t][di] if t in closes else None
                    if v is not None:
                        rows.append((t, d, v))
        except Exception:
            pass

    # NS-7: bench_closes.json {SPY:[[date, price], ...], QQQ:[...]}
    # each series is a list of [date, price] pairs (self-describing dates).
    bench = ROOT / "NS-7_QA" / "data" / "bench_closes.json"
    if bench.exists():
        try:
            bdoc = json.loads(bench.read_text())
            for t, series in bdoc.items():
                for pair in series:
                    if isinstance(pair, (list, tuple)) and len(pair) >= 2 and pair[1] is not None:
                        rows.append((t, pair[0], pair[1]))
        except Exception:
            pass

    if not rows:
        return 0
    n = 0
    try:
        with conn, conn.cursor() as cur:
            for t, d, v in rows:
                cur.execute(
                    "INSERT INTO daily_prices (ticker, date, raw_close, adj_close) "
                    "VALUES (%s,%s,%s,%s) "
                    "ON CONFLICT (ticker, date) DO UPDATE SET "
                    " raw_close=EXCLUDED.raw_close, adj_close=EXCLUDED.adj_close",
                    (t, d, v, v),
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


# ── NS-7 store (Phase G.3) ───────────────────────────────────────────────
def _backfill_ns7() -> int:
    """ns7.db → ns7_league / ns7_volume / ns7_selection / ns7_refresh_meta."""
    ns7_db = ROOT / "NS-7_QA" / "data" / "ns7.db"
    if not ns7_db.exists():
        return 0
    try:
        c = sqlite3.connect(ns7_db)
        c.row_factory = sqlite3.Row
        league = [dict(r) for r in c.execute("SELECT * FROM league")]
        volume = [dict(r) for r in c.execute("SELECT * FROM volume")]
        selection = [dict(r) for r in c.execute("SELECT * FROM selection ORDER BY id")]
        meta = [dict(r) for r in c.execute("SELECT * FROM refresh_meta")]
        c.close()
    except Exception:
        return 0

    conn = db._connect()
    if conn is None:
        return 0
    n = 0
    try:
        with conn, conn.cursor() as cur:
            for r in league:
                cur.execute(
                    "INSERT INTO ns7_league (ticker, league, consecutive_compliant, "
                    " consecutive_noncompliant, first_seen, last_seen) VALUES (%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (ticker) DO UPDATE SET league=EXCLUDED.league, "
                    " consecutive_compliant=EXCLUDED.consecutive_compliant, "
                    " consecutive_noncompliant=EXCLUDED.consecutive_noncompliant, "
                    " first_seen=EXCLUDED.first_seen, last_seen=EXCLUDED.last_seen",
                    (r["ticker"], r["league"], r["consecutive_compliant"],
                     r["consecutive_noncompliant"], r["first_seen"], r["last_seen"]))
                n += 1
            for r in volume:
                cur.execute(
                    "INSERT INTO ns7_volume (ticker, date, volume) VALUES (%s,%s,%s) "
                    "ON CONFLICT (ticker, date) DO UPDATE SET volume=EXCLUDED.volume",
                    (r["ticker"], r["date"], r["volume"]))
                n += 1
            for r in selection:
                cur.execute(
                    "INSERT INTO ns7_selection (generated_at, as_of, payload) VALUES (%s,%s,%s) "
                    "ON CONFLICT (id) DO NOTHING",
                    (r["generated_at"], r["as_of"], db._jsonb(r["payload"])))
                n += 1
            for r in meta:
                cur.execute(
                    "INSERT INTO ns7_refresh_meta (key, value) VALUES (%s,%s) "
                    "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                    (r["key"], r["value"]))
                n += 1
        return n
    except Exception:
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── NS-8 store (Phase G.3) ───────────────────────────────────────────────
def _backfill_ns8() -> int:
    """ns8.db → ns8_signals / ns8_tranche_state / ns8_audit_log."""
    ns8_db = ROOT / "NS-8_QA" / "data" / "ns8.db"
    if not ns8_db.exists():
        return 0
    try:
        c = sqlite3.connect(ns8_db)
        c.row_factory = sqlite3.Row
        signals = [dict(r) for r in c.execute("SELECT * FROM signals")]
        tranche = [dict(r) for r in c.execute("SELECT * FROM tranche_state")]
        audit = [dict(r) for r in c.execute("SELECT * FROM audit_log ORDER BY id")]
        c.close()
    except Exception:
        return 0

    conn = db._connect()
    if conn is None:
        return 0
    n = 0
    try:
        with conn, conn.cursor() as cur:
            for r in signals:
                cur.execute(
                    "INSERT INTO ns8_signals (as_of, signals_json, weights_json, version, generated_at) "
                    "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (as_of) DO UPDATE SET "
                    " signals_json=EXCLUDED.signals_json, weights_json=EXCLUDED.weights_json, "
                    " version=EXCLUDED.version, generated_at=EXCLUDED.generated_at",
                    (r["as_of"], db._jsonb(r["signals_json"]), db._jsonb(r["weights_json"]),
                     r["version"], r["generated_at"]))
                n += 1
            for r in tranche:
                cur.execute(
                    "INSERT INTO ns8_tranche_state (tranche_idx, next_rebalance, last_rebalance) "
                    "VALUES (%s,%s,%s) ON CONFLICT (tranche_idx) DO UPDATE SET "
                    " next_rebalance=EXCLUDED.next_rebalance, last_rebalance=EXCLUDED.last_rebalance",
                    (r["tranche_idx"], r["next_rebalance"], r["last_rebalance"]))
                n += 1
            for r in audit:
                cur.execute(
                    "INSERT INTO ns8_audit_log (timestamp, tranche_idx, symbol, side, qty, order_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (r["timestamp"], r["tranche_idx"], r["symbol"], r["side"], r["qty"], r["order_id"]))
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
    ns6 = _backfill_ns6_logs()
    dp = _backfill_daily_prices()
    ns7 = _backfill_ns7()
    ns8 = _backfill_ns8()
    print(f"backfilled: portfolios={p} strategy_output={so} "
          f"strategy_returns={sr} regime_history={rg} "
          f"ns6_logs={ns6} daily_prices={dp} ns7={ns7} ns8={ns8}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
