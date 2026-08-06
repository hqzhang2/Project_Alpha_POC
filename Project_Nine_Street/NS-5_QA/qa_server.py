#!/usr/bin/env python3
"""
NS-5 QA Server (Phase 1 stub) — stdlib http.server + factor pipeline.

Phase 1 endpoints:
  /health            -> 200 + factor data freshness
  /api/factors       -> factor returns summary (latest values per factor)
  /api/environment   -> vol/correlation environment snapshot

Phase 2+ will add /api/grade (concentration consumer). Per roadmap Phase 5:
QA runs on port 9251; PROD deferred until v1 stable.
"""
import json
import logging
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pandas as pd

import config
import data_fetcher
import environment

PORT = int(os.environ.get("PORT", 9251))
ENV = os.environ.get("ENV", "QA")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("ns5.qa_server")

_factors_cache = None
_factors_ts = None
FACTORS_CACHE_TTL = 300  # seconds


def _get_factors():
    """Cached factor returns (no Yahoo on the hot path — data pre-cached by cron)."""
    global _factors_cache, _factors_ts
    now = datetime.now()
    if _factors_cache is not None and _factors_ts is not None:
        if (now - _factors_ts).total_seconds() < FACTORS_CACHE_TTL:
            return _factors_cache
    factors, closes, rf = data_fetcher.build_factor_returns()
    _factors_cache = factors
    _factors_ts = now
    return factors


def _freshness_meta():
    """Factor data freshness from data/factor_meta.json if present."""
    meta_path = config.DATA_DIR / "factor_meta.json"
    if meta_path.exists():
        with open(meta_path) as fh:
            return json.load(fh)
    return {"error": "no factor_meta.json — run refresh"}


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, status=200):
        body = json.dumps(obj, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            if self.path in ("/health", "/health/"):
                meta = _freshness_meta()
                factors = _get_factors()
                self._json({
                    "status": "ok",
                    "service": "ns5",
                    "env": ENV,
                    "port": PORT,
                    "factor_rows": int(factors.shape[0]) if not factors.empty else 0,
                    "factor_last_date": str(factors.index[-1].date()) if not factors.empty else None,
                    "factor_meta": meta,
                    "as_of": datetime.now(timezone.utc).isoformat(),
                })
            elif self.path.startswith("/api/factors"):
                factors = _get_factors()
                if factors.empty:
                    self._json({"error": "no factor data"}, 503)
                    return
                latest = factors.iloc[-1]
                self._json({
                    "as_of": str(factors.index[-1].date()),
                    "latest_daily_returns": {k: float(v) for k, v in latest.items()},
                    "row_count": int(factors.shape[0]),
                })
            elif self.path.startswith("/api/environment"):
                factors = _get_factors()
                if factors.empty:
                    self._json({"error": "no factor data"}, 503)
                    return
                summary = environment.environment_summary(factors)
                self._json(summary)
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:  # noqa: BLE001 — return structured error, never crash
            log.exception("handler error")
            self._json({"error": str(exc)}, 500)

    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)


def main():
    log.info("NS-5 QA server starting on port %d (env=%s)", PORT, ENV)
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
