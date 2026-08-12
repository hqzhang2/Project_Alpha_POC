#!/usr/bin/env python3
"""
NS-6 QA Server — stdlib http.server for the Drawdown Engine & Scenario Cockpit.

Endpoints:
  GET  /health                      -> 200 + env/port
  GET  /api/enforcement/status      -> drawdown budget + exposure multiplier + active profile + regime switch suggestion
  GET  /api/profile                 -> list available profiles + active
  POST /api/profile                 -> set active profile (body: {profile: "growth"})
  GET  /api/drift                   -> drift alerts (quarterly check)
  POST /api/scenario/add            -> add-stock scenario (JSON body)
  POST /api/scenario/remove         -> remove-stock scenario (JSON body)
  POST /api/scenario/replace        -> replace scenario (JSON body)

QA on port 9261; PROD on 9260 (env-derived PORT).
CORS emitted ONLY in end_headers() (single source — double header breaks
portal health). See ns5-portfolio-governance skill.
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# Repo-root sys.path bootstrap — shared common/ + env -u PYTHONPATH at runtime.
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common import regime_store as regime_store_mod

import budget as budget_mod
import config
import drift_alert as drift_mod
import enforcement as enforcement_mod
import rebalance as rebalance_mod
import scenario as scenario_mod
import store

PORT = int(os.environ.get("PORT", 9261))
ENV = os.environ.get("ENV", "QA")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("ns6.qa_server")

# Default demo portfolio weights (for /api/drift + /api/enforcement/status
# when no stored portfolio exists yet).
DEFAULT_WEIGHTS = {
    "AAPL": 0.12, "MSFT": 0.10, "NVDA": 0.08, "GOOGL": 0.07,
    "AMZN": 0.06, "META": 0.05, "JPM": 0.05, "XOM": 0.04,
    "TLT": 0.20, "GLD": 0.10, "BIL": 0.13,
}


class NS6Handler(BaseHTTPRequestHandler):
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

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return {}

    # ── Routes ───────────────────────────────────────────────────────────
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/health":
            return self._json({"status": "ok", "service": "NS-6", "env": ENV,
                               "port": PORT})
        if path == "/api/enforcement/status":
            return self._enforcement_status()
        if path == "/api/profile":
            return self._profile_get()
        if path == "/api/drift":
            return self._drift()
        self._json({"error": f"not found: {path}"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._read_body()
        if path == "/api/scenario/add":
            return self._scenario_add(body)
        if path == "/api/scenario/remove":
            return self._scenario_remove(body)
        if path == "/api/scenario/replace":
            return self._scenario_replace(body)
        if path == "/api/profile":
            return self._profile_set(body)
        self._json({"error": f"not found: {path}"}, 404)

    # ── Handlers ─────────────────────────────────────────────────────────
    def _portfolio_weights(self, body):
        """Extract current_weights from body or fall back to DEFAULT_WEIGHTS."""
        w = body.get("current_weights")
        if isinstance(w, dict) and w:
            return w
        return DEFAULT_WEIGHTS

    def _regime_suggestion(self, active_profile):
        """Advisory profile switch suggestion from the macro regime axis.

        Returns (suggested_profile, reason, regime_code). suggested_profile is
        None when there's no fresh regime data or the regime is unknown —
        advisory only, never auto-switch.
        """
        row = regime_store_mod.latest()
        if row is None:
            return None, "no regime data", None
        regime = row.get("regime")
        # Staleness guard: don't nudge on old macro reads.
        try:
            recorded = datetime.fromisoformat(row["recorded_at"].replace("Z", "+00:00"))
        except (KeyError, ValueError, TypeError):
            recorded = None
        if recorded is None or (datetime.now(timezone.utc) - recorded).days > config.REGIME_MAX_AGE_DAYS:
            return None, f"regime data stale (> {config.REGIME_MAX_AGE_DAYS}d)", regime
        suggested = config.suggest_profile(regime)
        if suggested is None:
            return None, f"regime {regime}: unknown", regime
        reason = f"regime {regime}: {config.PROFILES[suggested]['label']}"
        return suggested, reason, regime

    def _enforcement_status(self):
        active_profile = store.get_active_profile()
        theta = config.load_profile(active_profile)[0]  # profile theta (overrides applied)
        latest = store.latest()
        # Phase 1: no live price ingestion — use last stored row or defaults.
        if latest:
            budget_remaining = latest.get("budget_remaining_pct", 1.0)
            current_dd = latest.get("portfolio_dd_pct", 0.0)
            spy_dd = latest.get("spy_dd_pct", 0.0)
            budget_pct = latest.get("budget_pct", 0.0)
        else:
            budget_remaining = 1.0
            current_dd = 0.0
            spy_dd = 0.0
            budget_pct = budget_mod.compute_budget(spy_dd, theta)

        multiplier = enforcement_mod.compute_exposure_multiplier(budget_remaining, theta)

        suggested, suggestion_reason, regime = self._regime_suggestion(active_profile)
        suggestion_active = bool(
            suggested and suggested != active_profile
        )

        self._json({
            "active_profile": active_profile,
            "profile_label": config.PROFILES[active_profile]["label"],
            "suggested_profile": suggested,
            "suggestion_reason": suggestion_reason,
            "suggestion_active": suggestion_active,
            "regime": regime or "R1",  # real regime code when available
            "spy_drawdown_pct": round(spy_dd, 4),
            "budget_pct": round(budget_pct, 4),
            "current_drawdown_pct": round(current_dd, 4),
            "budget_remaining_pct": round(budget_remaining, 4),
            "exposure_multiplier": round(multiplier, 4),
            "active_tiers": [],
            "covered_calls_gated": multiplier < theta["covered_calls"]["gate_multiplier"],
            "protective_puts": None,
            "circuit_breakers": [],
            "position_stops_triggered": [],
            "last_breaker_time": None,
            "phase": 1,
            "note": "Phase 1: budget-only multiplier. Price ingestion lands in Phase 2.",
        })

    def _profile_get(self):
        active = store.get_active_profile()
        available = [
            {"name": name, "label": p["label"], "description": p["description"]}
            for name, p in config.PROFILES.items()
        ]
        self._json({"active_profile": active,
                    "profile_label": config.PROFILES[active]["label"],
                    "available": available})

    def _profile_set(self, body):
        name = str(body.get("profile", "")).strip().lower()
        if name not in config.PROFILES:
            return self._json({"error": f"unknown profile '{name}'",
                               "valid": sorted(config.PROFILES)}, 400)
        saved = store.set_active_profile(name)
        self._json({"active_profile": saved,
                    "profile_label": config.PROFILES[saved]["label"],
                    "ok": True})

    def _drift(self):
        theta = config.load_theta()
        current = self._portfolio_weights({})
        # Phase 1: no frontier targets — use default as a stand-in.
        target = {k: v for k, v in DEFAULT_WEIGHTS.items()}
        result = drift_mod.run_drift_check(current, target, theta=theta)
        self._json(result)

    def _scenario_add(self, body):
        theta = config.load_theta()
        ticker = str(body.get("ticker", "")).strip().upper()
        if not ticker:
            return self._json({"error": "ticker required"}, 400)
        current = self._portfolio_weights(body)
        nav = float(body.get("nav", 1_000_000))
        proposed = float(body.get("proposed_weight", 0.03))
        prices = body.get("prices") or {}
        result = scenario_mod.analyze_add(
            ticker, proposed, current, nav,
            prices=prices, screener_scores=body.get("screener_scores"),
            ns2_regimes=body.get("ns2_regimes"), theta=theta)
        self._json(result)

    def _scenario_remove(self, body):
        theta = config.load_theta()
        ticker = str(body.get("ticker", "")).strip().upper()
        if not ticker:
            return self._json({"error": "ticker required"}, 400)
        current = self._portfolio_weights(body)
        nav = float(body.get("nav", 1_000_000))
        result = scenario_mod.analyze_remove(
            ticker, current, nav, prices=body.get("prices") or {},
            screener_scores=body.get("screener_scores"),
            ns2_regimes=body.get("ns2_regimes"), theta=theta)
        self._json(result)

    def _scenario_replace(self, body):
        theta = config.load_theta()
        add = str(body.get("add", "")).strip().upper()
        rem = str(body.get("remove", "")).strip().upper()
        if not add or not rem:
            return self._json({"error": "both 'add' and 'remove' required"}, 400)
        current = self._portfolio_weights(body)
        nav = float(body.get("nav", 1_000_000))
        proposed = float(body.get("proposed_weight", 0.03))
        result = scenario_mod.analyze_replace(
            rem, add, proposed, current, nav,
            prices=body.get("prices") or {},
            screener_scores=body.get("screener_scores"),
            ns2_regimes=body.get("ns2_regimes"), theta=theta)
        self._json(result)


def main():
    store.init_db()
    log.info("NS-6 %s server on port %d", ENV, PORT)
    HTTPServer(("0.0.0.0", PORT), NS6Handler).serve_forever()


if __name__ == "__main__":
    main()
