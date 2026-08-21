"""NS-ETF QA/PROD server — stdlib http.server, ROUTES registry (NS-7 pattern).

Ports: QA 9293 / PROD 9292. Single-origin dashboard + JSON API.
CORS from end_headers() ONLY.
"""
import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import config
import pipeline
import store

BASE = Path(__file__).resolve().parent
DASHBOARD = BASE / "nsetf_dashboard.html"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):   # quiet (arg name must match base)
        pass

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, path):
        try:
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            self._json(404, {"error": "not found"})

    # ── routes ────────────────────────────────────────────────────────
    def do_GET(self):
        route = self.path.split("?")[0].rstrip("/") or "/"
        if route in ("/", "/index.html"):
            self._html(DASHBOARD)
        elif route == "/health":
            ok = config.SIGNALS_PATH.exists()
            self._json(200, {"service": "ns-etf", "status": "ok" if ok else "no-feed",
                             "port": int(os.environ.get("PORT", config.PORT_QA))})
        elif route == "/api/signals":
            if config.SIGNALS_PATH.exists():
                self._json(200, json.loads(config.SIGNALS_PATH.read_text()))
            else:
                self._json(503, {"error": "feed not built yet — run pipeline.py"})
        elif route == "/api/vix":
            spot, avg = pipeline.vix_snapshot()
            info = overlay_state(spot, avg)
            self._json(200, info)
        elif route == "/api/advisory":
            feed = _feed_or_none()
            panel = feed.get("advisory_sector_ratios", []) if feed else []
            self._json(200, {"ratios": panel,
                             "note": "advisory only — zero allocation impact"})
        elif route == "/api/performance":
            self._json(200, performance_snapshot())
        elif route == "/api/meta":
            conn = store._connect()
            meta = store.get_meta(conn, "last_run", {})
            conn.close()
            self._json(200, meta)
        else:
            self._json(404, {"error": f"unknown route {route}"})


def _feed_or_none():
    if config.SIGNALS_PATH.exists():
        return json.loads(config.SIGNALS_PATH.read_text())
    return None


def overlay_state(spot, avg):
    import overlay as ov
    return ov.vix_state(spot, avg)


def performance_snapshot():
    """Strategy-vs-SPY equity curve for the retained NS-1 chart.
    Computed from the sqlite price store (never hardcoded)."""
    conn = store._connect()
    try:
        store.init_db()
        spy = selector_series(conn, "SPY")
        book = book_curve(conn)
    except sqlite3.OperationalError:
        conn.close()
        return {"error": "price store not initialized — run pipeline.py first"}
    conn.close()
    dates = sorted(set(d for d, _ in spy) & set(d for d, _ in book))
    if not dates:
        return {"error": "insufficient stored history"}
    dset_spy = dict(spy)
    dset_book = dict(book)
    s0 = dset_spy[dates[0]]
    b0 = dset_book[dates[0]]
    vix = None
    try:
        spot, avg = pipeline.vix_snapshot()
        vix = {"spot": spot, "avg": avg}
    except Exception:
        pass
    return {
        "dates": dates[-500:],   # last ~2y of daily points
        "strategy": [round(dset_book[d] / b0, 4) for d in dates][-500:],
        "spy": [round(dset_spy[d] / s0, 4) for d in dates][-500:],
        "vix": vix,
    }


def selector_series(conn, ticker):
    rows = conn.execute("SELECT date, close FROM prices WHERE ticker=? ORDER BY date",
                        (ticker,)).fetchall()
    return [(r[0], r[1]) for r in rows]


def book_curve(conn):
    """Equal-weight buy&hold of the internal universe as a placeholder
    strategy curve; replaced by the walk-forward backtest output in Phase P2."""
    tickers = [r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM prices").fetchall()]
    per_ticker = {}
    for t in sorted(tickers):
        rows = conn.execute("SELECT date, close FROM prices WHERE ticker=? ORDER BY date",
                            (t,)).fetchall()
        per_ticker[t] = dict(rows)
    common = None
    for t, m in per_ticker.items():
        ks = set(m)
        common = ks if common is None else (common & ks)
    if not common:
        return []
    dates = sorted(common)
    out = {}
    n = len(per_ticker)
    for d in dates:
        out[d] = sum(per_ticker[t][d] for t in per_ticker) / n
    return list(out.items())


ROUTES = {
    "/health": lambda: None,          # documentation; dispatch is in do_GET
}


def main():
    port = int(os.environ.get("PORT", config.PORT_QA))
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"NS-ETF serving on :{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
