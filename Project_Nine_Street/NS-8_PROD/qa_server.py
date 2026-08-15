#!/usr/bin/env python3
"""
NS-8 Server — stdlib http.server for the Tactical Asset Allocation Service.

Endpoints:
  GET  /health                 -> status/env/port + config
  GET  /api/signals            -> latest signal document (signals + weights)
  GET  /api/tranche            -> tranche schedule + current tranche
  POST /api/rebalance          -> run one full pipeline refresh
  GET  /api/walkforward        -> run walkforward backtest (dev)
  GET  /                       -> ns8_dashboard.html

QA on port 9281; PROD on 9280 (env-derived PORT).
CORS emitted ONLY in end_headers() (single source — double header breaks
portal health). Matches the NS-7 stdlib http.server pattern so the service
runs on CLT python3.9 (FastAPI/pydantic is not reliable there).
"""

import json
import logging
import os
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import config
import pipeline
import store
import walkforward

PORT = int(os.environ.get("PORT", 9280))
ENV = os.environ.get("ENV", "PROD")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("ns8.qa_server")


def _serve_dashboard(handler):
    """Serve ns8_dashboard.html (the portal-facing UI)."""
    dash_path = Path(__file__).resolve().parent / "ns8_dashboard.html"
    if not dash_path.exists():
        handler._json({"error": "ns8_dashboard.html not found"}, 404)
        return
    with open(dash_path, "rb") as fh:
        body = fh.read()
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class NS8Handler(BaseHTTPRequestHandler):
    # ── HTTP plumbing ────────────────────────────────────────────────────
    def log_message(self, format, *args):  # quieter
        log.info("%s - %s", self.address_string(), format % args)

    def end_headers(self):
        # Single source of CORS (covers JSON, HTML, 404s).
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _json(self, obj, status=200):
        body = json.dumps(obj, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── Routes ───────────────────────────────────────────────────────────
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html", "/ns8_dashboard.html", "/dashboard"):
            return _serve_dashboard(self)
        if path == "/health":
            return self._health()
        if path == "/api/signals":
            return self._signals()
        if path == "/api/tranche":
            return self._tranche()
        if path == "/api/walkforward":
            return self._walkforward()
        self._json({"error": f"not found: {path}"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/rebalance":
            return self._rebalance()
        self._json({"error": f"not found: {path}"}, 404)

    # ── Handlers ─────────────────────────────────────────────────────────
    def _health(self):
        self._json({
            "status": "ok",
            "service": "NS-8",
            "env": ENV,
            "port": PORT,
            "config": {
                "sma_window": config.SMA_WINDOW,
                "tranches": config.TRANCHES,
                "risky_assets": config.RISKY_ASSETS,
                "cash_proxy": config.CASH_PROXY,
            },
            "methodology": "200-day SMA tactical allocation + 4-tranche weekly rebalance",
        })

    def _signals(self):
        signal = store.get_latest_signal()
        if not signal:
            return self._json({
                "error": "No signals generated yet. POST /api/rebalance first.",
            }, 404)
        signal["tranche"] = self._tranche_data()
        self._json(signal)

    def _tranche_data(self):
        state = store.get_tranche_state()
        current = store.get_current_tranche()
        schedule = [
            t.get("next_rebalance") for t in sorted(state, key=lambda x: x["tranche_idx"])
        ] if state else [None] * config.TRANCHES
        return {
            "current": current,
            "total": config.TRANCHES,
            "schedule": schedule,
        }

    def _tranche(self):
        self._json(self._tranche_data())

    def _rebalance(self):
        try:
            # Parse optional body (source / as_of)
            source = "yfinance"
            as_of = None
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    body = json.loads(self.rfile.read(length).decode())
                    source = body.get("source", source)
                    as_of = body.get("as_of")
            except Exception:  # noqa: BLE001 — malformed body → defaults
                pass
            doc = pipeline.run_refresh(as_of=as_of, source=source)
            return self._json({"status": "ok", "document": doc})
        except Exception as exc:  # noqa: BLE001
            log.exception("rebalance failed")
            return self._json({"error": f"Rebalance failed: {exc}"}, 500)

    def _walkforward(self):
        try:
            result = walkforward.run_walkforward()
            return self._json(result)
        except Exception as exc:  # noqa: BLE001
            log.exception("walkforward failed")
            return self._json({"error": f"Walkforward failed: {exc}"}, 500)


def main():
    store.init_db()
    store.init_tranche_state()
    log.info("NS-8 %s server on port %d", ENV, PORT)
    HTTPServer(("0.0.0.0", PORT), NS8Handler).serve_forever()


if __name__ == "__main__":
    main()
