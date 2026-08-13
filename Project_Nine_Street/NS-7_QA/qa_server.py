#!/usr/bin/env python3
"""
NS-7 QA Server — stdlib http.server for the Growth/Momentum Selection Service.

Endpoints (DESIGN.md §6):
  GET  /health                 -> status/env/port + last pipeline refresh
  GET  /api/universe           -> league counts + full membership
  GET  /api/major              -> Major-league tickers + momentum scores, ranked
  GET  /api/leagues/{ticker}   -> one ticker's league, tenure, grace status
  GET  /api/select             -> top-N ranked output (the NS-5 growth-sleeve feed)
  GET  /                       -> ns7_dashboard.html

QA on port 9271; PROD on 9270 (env-derived PORT).
CORS emitted ONLY in end_headers() (single source — double header breaks
portal health). See ns7-growth-momentum skill.
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

PORT = int(os.environ.get("PORT", 9271))
ENV = os.environ.get("ENV", "QA")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("ns7.qa_server")


def _serve_dashboard(handler):
    """Serve ns7_dashboard.html (the portal-facing UI)."""
    dash_path = Path(__file__).resolve().parent / "ns7_dashboard.html"
    if not dash_path.exists():
        handler._json({"error": "ns7_dashboard.html not found"}, 404)
        return
    with open(dash_path, "rb") as fh:
        body = fh.read()
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class NS7Handler(BaseHTTPRequestHandler):
    # ── HTTP plumbing ────────────────────────────────────────────────────
    def log_message(self, format, *args):  # quieter
        log.info("%s - %s", self.address_string(), format % args)

    def end_headers(self):
        # Single source of CORS (covers JSON, HTML, 404s).
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
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
        if path in ("/", "/index.html", "/ns7_dashboard.html"):
            return _serve_dashboard(self)
        if path == "/health":
            return self._health()
        if path == "/api/universe":
            return self._universe()
        if path == "/api/major":
            return self._major()
        if path == "/api/select":
            return self._select()
        if path.startswith("/api/leagues/"):
            ticker = path.split("/")[-1].strip().upper()
            if ticker:
                return self._league_detail(ticker)
        self._json({"error": f"not found: {path}"}, 404)

    # ── Handlers ─────────────────────────────────────────────────────────
    def _health(self):
        last = store.get_meta("last_refresh")
        u3_waived = store.get_meta("u3_waived")
        self._json({
            "status": "ok",
            "service": "NS-7",
            "env": ENV,
            "port": PORT,
            "last_refresh": last,
            "u3_waived_on": u3_waived,
            "methodology": "skip-month momentum 126/21 + quality veto + two-league grace",
        })

    def _universe(self):
        rows = store.all_leagues()
        counts = store.league_counts()
        # Compliance status is refreshed by the pipeline; surface tenure too.
        self._json({
            "as_of": store.get_meta("last_refresh"),
            "counts": counts,
            "members": rows,
        })

    def _major(self):
        """Major-league tickers + momentum scores, ranked (from the latest
        pipeline selection doc). Empty scores = no refresh yet or no Major."""
        latest = store.latest_selection()
        scores = (latest or {}).get("payload", {}).get("scores", [])
        major = {r["ticker"] for r in store.all_leagues()
                 if r["league"] == config.LEAGUE_MAJOR}
        # scores already only contain Major names (pipeline ranks Major only).
        self._json({
            "as_of": (latest or {}).get("as_of"),
            "major_count": len(major),
            "scored_count": len(scores),
            "scores": scores,
        })

    def _select(self):
        """The NS-5 growth-sleeve feed: top-N ranked output + metadata."""
        latest = store.latest_selection()
        if not latest:
            return self._json({"error": "no selection yet — run pipeline.py",
                               "as_of": None, "selections": []}, 404)
        self._json(latest["payload"])

    def _league_detail(self, ticker):
        """One ticker's stored league state + live point-in-time facts."""
        row = store.get_league(ticker)
        if row is None:
            return self._json({"ticker": ticker, "tracked": False,
                               "reason": "not in the tracked universe"}, 404)
        # Live compliance facts (best-effort; volume may lag the last refresh).
        as_of = store.get_meta("last_refresh") or datetime.now().strftime("%Y-%m-%d")
        try:
            facts = pipeline.facts_for(ticker, as_of, ticker in set(pipeline.sp500_current()))
            compliant = pipeline.eligible(facts)
        except Exception as exc:  # noqa: BLE001 — A_T store may be down
            log.warning("facts_for(%s) failed: %s", ticker, exc)
            facts, compliant = {}, None
        grace_left = max(
            0, config.GRACE_PERIOD_DAYS - int(row.get("consecutive_compliant", 0)))
        self._json({
            "ticker": ticker,
            "tracked": True,
            "league": row["league"],
            "consecutive_compliant": row["consecutive_compliant"],
            "consecutive_noncompliant": row["consecutive_noncompliant"],
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
            "compliant_today": compliant,
            "grace_days_left_to_promotion": grace_left if row["league"] == config.LEAGUE_MINOR else None,
            "facts": {k: facts.get(k) for k in
                      ("in_sp500", "market_cap", "eps_ttm", "cfo_ttm",
                       "avg_daily_volume", "snapshot_age_days")},
        })


def main():
    store.init_db()
    log.info("NS-7 %s server on port %d", ENV, PORT)
    HTTPServer(("0.0.0.0", PORT), NS7Handler).serve_forever()


if __name__ == "__main__":
    main()
