#!/usr/bin/env python3
"""
NS-X Server — stdlib http.server for the Strategy Allocation Service.

Endpoints:
  GET  /health           -> status/env/port + config
  GET  /api/alloc        -> latest strategy_alloc.json (the NS-5 input)
  GET  /api/registry     -> strategy registry + per-strategy momentum/source
  GET  /api/rotation     -> signal detail (scores, ranking, caps)
  POST /api/rebalance    -> run one full allocation refresh
  GET  /api/walkforward  -> run the rotation-vs-static validation (dev)
  GET  /                 -> nsx_dashboard.html

QA on port 9291; PROD on 9290 (env-derived PORT).
CORS emitted ONLY in end_headers() (single source — double header breaks portal
health). stdlib http.server so it runs on CLT python3.9, matching NS-7/NS-8.
"""
import json
import logging
import os
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import allocator
import config
import registry
import rotation

PORT = int(os.environ.get("PORT", 9291))
ENV = os.environ.get("ENV", "QA")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("nsx.qa_server")


def _serve_dashboard(handler):
    """Serve nsx_dashboard.html (the portal-facing UI)."""
    dash_path = Path(__file__).resolve().parent / "nsx_dashboard.html"
    if not dash_path.exists():
        handler._json({"error": "nsx_dashboard.html not found"}, 404)
        return
    with open(dash_path, "rb") as fh:
        body = fh.read()
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class NSXHandler(BaseHTTPRequestHandler):
    # ── HTTP plumbing ────────────────────────────────────────────────────
    def log_message(self, format, *args):  # quieter
        log.info("%s - %s", self.address_string(), format % args)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")   # single CORS source
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
        if path in ("/", "/index.html", "/nsx_dashboard.html", "/dashboard"):
            return _serve_dashboard(self)
        if path == "/health":
            return self._health()
        if path == "/api/alloc":
            return self._alloc()
        if path == "/api/registry":
            return self._registry()
        if path == "/api/rotation":
            return self._rotation()
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
            "service": "NS-X",
            "env": ENV,
            "port": PORT,
            "config": {
                "rotation": config.ROTATION,
                "risk_adjusted": config.RISK_ADJUST,
                "lookback": config.MOM_LOOKBACK_DAYS,
                "skip": config.MOM_SKIP_DAYS,
                "max_strategy_w": config.NSX_MAX_STRATEGY_W,
                "defensive_floor": config.NSX_DEFENSIVE_FLOOR,
            },
            "methodology": "risk-adjusted relative momentum across strategies",
        })

    def _alloc(self):
        if not config.ALLOC_PATH.exists():
            return self._json({"error": "No allocation yet. POST /api/rebalance first."}, 404)
        with open(config.ALLOC_PATH) as fh:
            self._json(json.load(fh))

    def _registry(self):
        reg = registry.build_registry()
        roles = {s.id: s.role for s in registry.enabled_registry()}
        momentum = {}
        for s in registry.enabled_registry():
            rets = registry.get_returns(s.id)
            momentum[s.id] = rotation.strategy_momentum(rets) if rets and len(rets) > 3 else None
        self._json({
            "registry": [
                {"id": s.id, "name": s.name, "role": s.role,
                 "cadence": s.cadence, "enabled": s.enabled}
                for s in reg
            ],
            "momentum_scores": {k: (round(v, 6) if v is not None else None)
                                for k, v in momentum.items()},
            "rotation": config.ROTATION,
        })

    def _rotation(self):
        enabled = registry.enabled_registry()
        roles = {s.id: s.role for s in enabled}
        streams = {s.id: registry.get_returns(s.id) for s in enabled}
        scores = {k: rotation.strategy_momentum(v) for k, v in streams.items()}
        weights = rotation.weight_strategies(scores, roles)
        self._json({
            "as_of": datetime.now().strftime("%Y-%m-%d"),
            "rotation": config.ROTATION,
            "risk_adjusted": config.RISK_ADJUST,
            "momentum_scores": {k: (round(v, 6) if v is not None else None)
                                for k, v in scores.items()},
            "strategies": weights,
            "weights_sum": round(sum(weights.values()), 12),
        })

    def _rebalance(self):
        try:
            as_of = None
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    body = json.loads(self.rfile.read(length).decode())
                    as_of = body.get("as_of")
            except Exception:  # noqa: BLE001
                pass
            doc = allocator.run_once(as_of)
            return self._json({"status": "ok", "document": doc})
        except Exception as exc:  # noqa: BLE001
            log.exception("rebalance failed")
            return self._json({"error": f"Rebalance failed: {exc}"}, 500)

    def _walkforward(self):
        try:
            import nsx_walkforward as wf
            res = wf.run_validation()
            return self._json(res)
        except Exception as exc:  # noqa: BLE001
            log.exception("walkforward failed")
            return self._json({"error": f"Walkforward failed: {exc}"}, 500)


def main():
    log.info("NS-X %s server on port %d", ENV, PORT)
    HTTPServer(("0.0.0.0", PORT), NSXHandler).serve_forever()


if __name__ == "__main__":
    main()
