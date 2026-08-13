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
from typing import Dict, Optional, Tuple

# Repo-root sys.path bootstrap — shared common/ + env -u PYTHONPATH at runtime.
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common import regime_store as regime_store_mod

import budget as budget_mod
import config
import drift_alert as drift_mod
import enforcement as enforcement_mod
import price_feed
import rebalance as rebalance_mod
import scenario as scenario_mod
import store

# NS-5's portfolio store is a JSON file (direct read, no import / no HTTP —
# keeps NS-6 fully decoupled; works even if NS-5 server is down).
NS5_PORTFOLIOS_PATH = (
    Path(__file__).resolve().parent.parent / "NS-5_QA" / "data" / "portfolios.json"
)
NS5_POLICIES_PATH = (
    Path(__file__).resolve().parent.parent / "NS-5_QA" / "data" / "policies.json"
)

PORT = int(os.environ.get("PORT", 9261))
ENV = os.environ.get("ENV", "QA")

# R2c: A_T fundamental-screener base URL, env-matched (QA A_T on 9099, PROD
# on 9098). Injected into the served dashboard so the scenario cockpit can
# show the real per-ticker screener verdict (dashboard→A_T, cross-origin;
# A_T sends CORS to localhost origins).
A_T_PORT = 9099 if ENV == "QA" else 9098
A_T_SCREENER_URL = f"http://localhost:{A_T_PORT}/api/fundamentals/screen"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("ns6.qa_server")

# Default demo portfolio weights (for /api/drift + /api/enforcement/status
# when no stored portfolio exists yet).
DEFAULT_WEIGHTS = {
    "AAPL": 0.12, "MSFT": 0.10, "NVDA": 0.08, "GOOGL": 0.07,
    "AMZN": 0.06, "META": 0.05, "JPM": 0.05, "XOM": 0.04,
    "TLT": 0.20, "GLD": 0.10, "BIL": 0.13,
}


def _dashboard_html() -> Optional[bytes]:
    """Read ns6_dashboard.html with the env-matched A_T screener URL injected
    (R2c). Returns None if the file is missing."""
    dash_path = Path(__file__).resolve().parent / "ns6_dashboard.html"
    if not dash_path.exists():
        return None
    with open(dash_path, "rb") as fh:
        body = fh.read()
    return body.replace(b"__AT_SCREENER_URL__", A_T_SCREENER_URL.encode())


def _serve_dashboard(handler):
    """Serve ns6_dashboard.html (the portal-facing UI)."""
    body = _dashboard_html()
    if body is None:
        handler._json({"error": "ns6_dashboard.html not found"}, 404)
        return
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


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
        if path in ("/", "/index.html", "/ns6_dashboard.html"):
            return _serve_dashboard(self)
        if path == "/health":
            return self._json({"status": "ok", "service": "NS-6", "env": ENV,
                               "port": PORT})
        if path == "/api/enforcement/status":
            return self._enforcement_status()
        if path == "/api/profile":
            return self._profile_get()
        if path == "/api/portfolio":
            return self._portfolio_get()
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
        if path == "/api/portfolio":
            return self._portfolio_set(body)
        if path == "/api/drift":
            return self._drift(body)
        self._json({"error": f"not found: {path}"}, 404)

    # ── Handlers ─────────────────────────────────────────────────────────
    def _portfolio_weights(self, body):
        """Extract current_weights from body, else the resolved portfolio
        source (NS-5 portfolio or model), else DEFAULT_WEIGHTS."""
        w = body.get("current_weights")
        if isinstance(w, dict) and w:
            return w
        _, _, weights, _ = self._portfolio_holdings()
        if weights:
            return dict(weights)
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
        # R2a: drawdown_log now populated by the daily price feed. If the feed
        # hasn't run, surface data_stale (never silently show 0.0).
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

        data_stale, data_as_of = price_feed.is_stale(
            latest, theta["price_feed"]["staleness_days"])

        multiplier = enforcement_mod.compute_exposure_multiplier(budget_remaining, theta)

        suggested, suggestion_reason, regime = self._regime_suggestion(active_profile)
        suggestion_active = bool(
            suggested and suggested != active_profile
        )
        port_source, port_is_model, _, _ = self._portfolio_holdings()

        self._json({
            "active_profile": active_profile,
            "profile_label": config.PROFILES[active_profile]["label"],
            "portfolio_source": port_source,
            "portfolio_is_model": port_is_model,
            "suggested_profile": suggested,
            "suggestion_reason": suggestion_reason,
            "suggestion_active": suggestion_active,
            "regime": regime,  # null when no regime row (don't fake "R1")
            "spy_drawdown_pct": round(spy_dd, 4),
            "budget_pct": round(budget_pct, 4),
            "current_drawdown_pct": round(current_dd, 4),
            "budget_remaining_pct": round(budget_remaining, 4),
            "data_as_of": data_as_of,
            "data_stale": data_stale,
            "exposure_multiplier": round(multiplier, 4),
            "active_tiers": [],
            "covered_calls_gated": multiplier < theta["covered_calls"]["gate_multiplier"],
            "protective_puts": None,
            "circuit_breakers": [],
            "position_stops_triggered": [],
            "last_breaker_time": None,
            "phase": 2,
            "note": "Drawdown data live via price feed (R2a). Multiplier still Phase-1 "
                    "budget-only (R3 fast de-risk pending).",
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

    def _active_theta(self):
        """Theta for the active profile (not the default)."""
        active = store.get_active_profile()
        return config.load_profile(active)[0]

    def _profile_context(self, theta):
        """Profile-dependent budget context, surfaced so the PM sees the
        effect of switching profiles (the profile overrides budget/de-risk)."""
        return {
            "active_profile": store.get_active_profile(),
            "hard_floor": round(theta["budget"]["hard_floor"], 4),
            "spy_dd_ratio": round(theta["budget"]["spy_dd_ratio"], 4),
            "crisis_floor": round(theta["fast_derisk"]["crisis_floor"], 4),
        }

    # ── Portfolio source (decoupled from NS-5) ─────────────────────────
    def _ns5_portfolios(self) -> Dict[str, dict]:
        """Raw NS-5 portfolio holdings {name: {ticker: shares}}. Fail-open."""
        try:
            if NS5_PORTFOLIOS_PATH.exists():
                with open(NS5_PORTFOLIOS_PATH) as fh:
                    data = json.load(fh)
                return data if isinstance(data, dict) else {}
        except Exception as exc:  # noqa: BLE001
            log.warning("read NS-5 portfolios failed: %s", exc)
        return {}

    def _portfolio_holdings(self):
        """Resolve the cockpit's current portfolio holdings + source info.

        Delegates to price_feed.resolve_holdings (the single source of the
        shares→weights normalization — shared with the daily price feed, R2a).
        """
        active = store.get_active_profile()
        source = store.get_portfolio_source()
        return price_feed.resolve_holdings(active, source, self._ns5_portfolios())

    def _portfolio_get(self):
        """GET /api/portfolio -> source, portfolio names (NS-5 + model),
        holdings WEIGHTS (engine) + raw SHARES (modal for NS-5)."""
        ns5 = self._ns5_portfolios()
        active = store.get_active_profile()
        source, is_model, weights, shares = self._portfolio_holdings()
        self._json({
            "active_profile": active,
            "source": source,
            "is_model": is_model,
            "holdings": {k: round(v, 6) for k, v in weights.items()},
            "shares": {k: round(v, 4) for k, v in shares.items()} if shares else None,
            "ns5_portfolios": sorted(ns5.keys()),
            "model_portfolios": {
                name: {k: round(v, 6) for k, v in config.model_portfolio(name).items()}
                for name in config.PROFILES
            },
        })

    def _portfolio_set(self, body):
        """POST /api/portfolio {source: 'model' | <ns5 portfolio name>}."""
        source = str(body.get("source", "")).strip()
        if source == store.MODEL_SOURCE:
            saved = store.set_portfolio_source(store.MODEL_SOURCE)
            return self._json({"ok": True, "source": saved, "is_model": True})
        ns5 = self._ns5_portfolios()
        if source not in ns5:
            return self._json({"error": f"unknown portfolio '{source}'",
                               "valid": sorted(ns5.keys()) + [store.MODEL_SOURCE]}, 400)
        saved = store.set_portfolio_source(source)
        self._json({"ok": True, "source": saved, "is_model": False})

    def _ns5_policies(self) -> Dict[str, dict]:
        """NS-5 policy store {name: weights} (values may be JSON strings)."""
        try:
            if NS5_POLICIES_PATH.exists():
                with open(NS5_POLICIES_PATH) as fh:
                    raw = json.load(fh)
                out = {}
                for name, v in raw.items():
                    if isinstance(v, str):
                        try:
                            v = json.loads(v)
                        except ValueError:
                            continue
                    if isinstance(v, dict):
                        out[str(name)] = {str(k): float(w) for k, w in v.items()}
                return out
        except (OSError, ValueError, TypeError) as exc:
            log.warning("NS-5 policies read failed: %s", exc)
        return {}

    def _drift_target(self) -> Tuple[Dict[str, float], str]:
        """The drift TARGET: the selected portfolio's policy (option 2).

        Resolution (PM decision 2026-08-13): portfolio_source → config
        PORTFOLIO_POLICIES → NS-5 policy weights. Fallbacks: portfolio not
        paired or policy missing → DEFAULT_WEIGHTS; model source → default.
        Returns (target_weights, target_source_label).
        """
        source = store.get_portfolio_source()
        if source != store.MODEL_SOURCE:
            policy_name = config.PORTFOLIO_POLICIES.get(source)
            if policy_name:
                policies = self._ns5_policies()
                weights = policies.get(policy_name)
                if weights:
                    return weights, f"policy:{policy_name}"
                log.warning("policy '%s' for portfolio '%s' not in NS-5 store; "
                            "using DEFAULT_WEIGHTS", policy_name, source)
        return dict(DEFAULT_WEIGHTS), "default"

    def _drift(self, body=None):
        theta = self._active_theta()
        current = self._portfolio_weights(body or {})
        target, target_source = self._drift_target()
        result = drift_mod.run_drift_check(current, target, theta=theta)
        result["profile_context"] = self._profile_context(theta)
        result["target_source"] = target_source
        self._json(result)

    def _scenario_add(self, body):
        theta = self._active_theta()
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
        result["profile_context"] = self._profile_context(theta)
        self._json(result)

    def _scenario_remove(self, body):
        theta = self._active_theta()
        ticker = str(body.get("ticker", "")).strip().upper()
        if not ticker:
            return self._json({"error": "ticker required"}, 400)
        current = self._portfolio_weights(body)
        nav = float(body.get("nav", 1_000_000))
        result = scenario_mod.analyze_remove(
            ticker, current, nav, prices=body.get("prices") or {},
            screener_scores=body.get("screener_scores"),
            ns2_regimes=body.get("ns2_regimes"), theta=theta)
        result["profile_context"] = self._profile_context(theta)
        self._json(result)

    def _scenario_replace(self, body):
        theta = self._active_theta()
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
        result["profile_context"] = self._profile_context(theta)
        self._json(result)


def main():
    store.init_db()
    log.info("NS-6 %s server on port %d", ENV, PORT)
    HTTPServer(("0.0.0.0", PORT), NS6Handler).serve_forever()


if __name__ == "__main__":
    main()
