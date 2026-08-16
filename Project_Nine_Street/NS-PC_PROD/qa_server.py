#!/usr/bin/env python3
"""
NS-PC Server — stdlib http.server for the Portfolio Constructor.

Endpoints:
  GET  /health        -> status/env/port
  GET  /portfolio     -> current paper_portfolio.json
  GET  /targets       -> the composed fund book (weights + guardrails)
  POST /construct     -> fetch prices, run constructor, write portfolio
  GET  /              -> nspc_dashboard.html

QA on port 9301; PROD on 9300 (env-derived PORT).
stdlib http.server (runs on CLT py3.9, mirrors NS-6/7/8/X). CORS single-source.
"""
import json
import logging
import os
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import config
import constructor

# repo root on sys.path so `import common.db` resolves (this service runs with
# NS-PC/ as cwd; common/ lives at the repo root)
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

PORT = int(os.environ.get("PORT", 9301))
ENV = os.environ.get("ENV", "QA")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("nspc.qa_server")


def _fetch_prices(tickers):
    """Last close for each ticker via yfinance (fallback: prior current_price)."""
    prices = {}
    try:
        import yfinance as yf
        for t in tickers:
            try:
                h = yf.Ticker(t).history(period="5d")
                if not h.empty:
                    prices[t] = float(h["Close"].iloc[-1])
            except Exception:
                continue
    except Exception as e:
        log.warning("yfinance unavailable: %s", e)
    return prices


def _serve_dashboard(handler):
    dash_path = Path(__file__).resolve().parent / "nspc_dashboard.html"
    if not dash_path.exists():
        handler._json({"error": "nspc_dashboard.html not found"}, 404)
        return
    with open(dash_path, "rb") as fh:
        body = fh.read()
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class NSPCHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        log.info("%s - %s", self.address_string(), format % args)

    def end_headers(self):
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

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html", "/nspc_dashboard.html", "/dashboard"):
            return _serve_dashboard(self)
        if path == "/health":
            return self._json({"status": "ok", "service": "NS-PC", "env": ENV,
                               "port": PORT, "cash_proxy": config.CASH_PROXY})
        if path == "/portfolio":
            return self._portfolio()
        if path == "/targets":
            return self._targets()
        self._json({"error": f"not found: {path}"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/construct":
            return self._construct()
        self._json({"error": f"not found: {path}"}, 404)

    def _portfolio(self):
        try:
            import common.db as db
            doc = db.get_portfolio(config.PORTFOLIO_NAME)
            if doc:
                return self._json(doc)
        except Exception:
            pass
        if not config.PORTFOLIO_PATH.exists():
            return self._json({"error": "No portfolio yet. POST /construct first."}, 404)
        with open(config.PORTFOLIO_PATH) as fh:
            self._json(json.load(fh))

    def _targets(self):
        alloc, blend, signals = constructor.read_inputs()
        if alloc is None or blend is None or signals is None:
            return self._json({"error": "missing/stale inputs"}, 503)
        w = constructor.apply_guards(constructor.compose(alloc, blend, signals))
        self._json({"weights": w, "guardrails": constructor.guardrails(w)})

    def _construct(self):
        try:
            # gather the ticker universe from the three inputs
            alloc, blend, signals = constructor.read_inputs()
            if alloc is None or blend is None or signals is None:
                return self._json({"error": "missing/stale inputs (no write)"}, 503)
            composed = constructor.apply_guards(constructor.compose(alloc, blend, signals))
            tickers = list(composed.keys())
            prices = _fetch_prices(tickers)
            # fallback: prior current_price for any ticker still missing
            prior = json.loads(config.PORTFOLIO_PATH.read_text()) if config.PORTFOLIO_PATH.exists() else None
            if prior:
                for t, p in prior.get("positions", {}).get("equities", {}).items():
                    if t not in prices:
                        prices[t] = p.get("current_price")
            doc = constructor.build_portfolio(alloc, blend, signals, prices, prior=prior)
            constructor.write_portfolio(doc)
            return self._json({"status": "ok",
                               "positions": len(doc["positions"]["equities"]),
                               "guardrails": doc.get("guardrails")})
        except Exception as exc:  # noqa: BLE001
            log.exception("construct failed")
            return self._json({"error": f"Construct failed (no write): {exc}"}, 500)


def main():
    log.info("NS-PC %s server on port %d", ENV, PORT)
    HTTPServer(("0.0.0.0", PORT), NSPCHandler).serve_forever()


if __name__ == "__main__":
    main()
