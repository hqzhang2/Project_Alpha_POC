#!/usr/bin/env python3
"""
NS-5 QA Server — stdlib http.server + factor pipeline + concentration grading.

Endpoints:
  GET  /health            -> 200 + factor data freshness
  GET  /api/factors       -> factor returns summary (latest values per factor)
  GET  /api/environment   -> vol/correlation environment snapshot
  GET  /api/grade         -> instructions + example payload
  POST /api/grade         -> concentration grade scorecard (JSON body)

Roadmap Phase 5: QA on port 9251; PROD deferred until v1 stable.
"""
import json
import logging
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import concentration
import config
import data_fetcher
import environment
import portfolio
import theta as theta_mod

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
                self._json({"status": "ok", "service": "ns5", "env": ENV, "port": PORT,
                            "factor_rows": int(factors.shape[0]) if not factors.empty else 0,
                            "factor_last_date": str(factors.index[-1].date()) if not factors.empty else None,
                            "factor_meta": meta, "as_of": datetime.now(timezone.utc).isoformat()})
            elif self.path.startswith("/api/factors"):
                factors = _get_factors()
                if factors.empty:
                    self._json({"error": "no factor data"}, 503); return
                latest = factors.iloc[-1]
                self._json({"as_of": str(factors.index[-1].date()),
                            "latest_daily_returns": {k: float(v) for k, v in latest.items()},
                            "row_count": int(factors.shape[0])})
            elif self.path.startswith("/api/environment"):
                factors = _get_factors()
                if factors.empty:
                    self._json({"error": "no factor data"}, 503); return
                self._json(environment.environment_summary(factors))
            elif self.path.startswith("/api/grade"):
                self._json({"usage": "POST /api/grade JSON: {holdings: {TICKER: weight}, "
                                      "policy_weights: {TICKER: weight}}",
                            "example": {"holdings": {"AAPL": 0.14, "MSFT": 0.12, "NVDA": 0.08,
                                                      "TLT": 0.30, "JPM": 0.05},
                                        "policy_weights": {"SPY": 0.60, "TLT": 0.40}}})
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:
            log.exception("handler error")
            self._json({"error": str(exc)}, 500)

    def do_POST(self):
        try:
            if self.path.startswith("/api/grade"):
                length = int(self.headers.get("Content-Length", 0))
                if length == 0:
                    self._json({"error": "empty body"}, 400); return
                body = json.loads(self.rfile.read(length))
                holdings = body.get("holdings", {})
                if not holdings:
                    self._json({"error": "missing holdings"}, 400); return
                theta = theta_mod.load_theta()
                if "policy_weights" in body:
                    theta["policy_weights"] = body["policy_weights"]
                if "max_single_name_pct" in body:
                    theta["max_single_name_pct"] = body["max_single_name_pct"]
                factors = _get_factors()
                if factors.empty:
                    self._json({"error": "no factor data"}, 503); return
                result = concentration.run_concentration_grade(
                    holdings, theta, factor_returns=factors)
                self._json(result)
            else:
                self._json({"error": "not found"}, 404)
        except json.JSONDecodeError as exc:
            self._json({"error": f"invalid JSON: {exc}"}, 400)
        except Exception as exc:
            log.exception("POST handler error")
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