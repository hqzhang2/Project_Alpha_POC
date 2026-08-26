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
import universe
import vs_badges

PORT = int(os.environ.get("PORT", 9271))
ENV = os.environ.get("ENV", "QA")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("ns7.qa_server")

# v4.6: 60s cache for the estimated D1 series (repeated modal opens).
_D1_SERIES_CACHE = {"data": None, "ts": 0.0}


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
        if path == "/api/vsbadges":
            return self._vsbadges()
        if path == "/api/d1":
            return self._d1()
        if path == "/api/d1/series":
            return self._d1_series()
        if path == "/api/d1/tenure":
            return self._d1_tenure()
        if path.startswith("/api/leagues/"):
            ticker = path.split("/")[-1].strip().upper()
            if ticker:
                return self._league_detail(ticker)
        self._json({"error": f"not found: {path}"}, 404)

    def do_POST(self):
        """v4.6: rebuild the D1 basket with a PM-chosen method/n."""
        path = self.path.split("?")[0]
        try:
            if path != "/api/d1/rebuild":
                self._json({"error": f"not found: {path}"}, 404); return
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            method = body.get("method") or config.D1_WEIGHT_METHOD
            n = body.get("n")
            n = int(n) if n else config.BASKET_TOP_N
            if not 1 <= n <= 100:
                self._json({"error": "n must be 1..100"}, 400); return

            import d1_basket, d1_grading
            closes = d1_basket._load_basket_closes()
            doc = d1_basket.build_basket(method=method, n=n,
                                         closes_by_ticker=closes)
            if doc is None:
                self._json({"error": "no selection available"}, 400); return
            # v4.6: optional manual stock filter — only `keep` names survive,
            # remaining weights renormalized to 100% (0% → excluded → '–').
            # v4.9: keep=absent/null → all selected; keep=[] → rejected (an
            # empty basket), via the shared pure helper apply_keep.
            keep = body.get("keep")
            if keep is not None:
                kept_w, err = d1_basket.apply_keep(doc["weights"], keep)
                if err:
                    self._json({"error": err}, 400); return
                original_n = len(doc["weights"])
                doc["weights"] = kept_w
                doc["top_n"] = len(doc["weights"])
                doc["eff_n"] = round(
                    1.0 / sum((w) ** 2 for w in doc["weights"].values()), 2)
                doc["max_weight"] = round(max(doc["weights"].values()), 6)
                doc["filtered_from"] = original_n
            Path(config.D1_BASKET_PATH).write_text(json.dumps(doc, indent=2))
            # v4.8: persist PM intent so headless rebuilds (daily 17:30 refresh,
            # deploys) reproduce THIS book instead of resetting to config
            # defaults. keep=None means all-selected (no filter).
            d1_basket.save_overrides(method, n, keep if keep else None)
            rows = d1_grading.mark_to_market()      # refresh the stream too
            if rows is not None:
                d1_grading.persist_returns(rows)
            doc["mtm_written"] = rows is not None
            self._json(doc)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:
            log.exception("d1 rebuild failed")
            self._json({"error": str(exc)}, 500)

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

    def _vsbadges(self):
        """Daily badge snapshot (HMM + value screen) — {} when absent/stale."""
        snap = vs_badges.load_snapshot()
        self._json(snap or {"tickers": {}})

    def _d1(self):
        """v4.6: the DeltaOne basket doc — {} when absent (no basket yet).
        v4.8: attach the PM-intent overrides doc (keep list hydrates the
        construction checkboxes; method/n are already in the basket)."""
        import d1_basket
        try:
            doc = json.loads(config.D1_BASKET_PATH.read_text())
        except Exception:
            self._json({"error": "no d1_basket yet"}); return
        ov = d1_basket.load_overrides()
        if ov:
            doc["overrides"] = {"keep": ov.get("keep"),
                                "as_of": ov.get("as_of")}
        self._json(doc)

    def _d1_series(self):
        """v4.6: D1 basket daily returns + SPY/VIX overlay for the 1-yr modal.

        Uses the FULL overlap window (from_as_of=False) — this is the
        *estimated* current-book curve, explicitly labeled, NOT the realized
        strategy_returns stream. Cached 60s so repeated modal opens don't
        recompute the price queries.
        """
        import time
        now = time.time()
        if (_D1_SERIES_CACHE["data"] is not None
                and now - _D1_SERIES_CACHE["ts"] < 60):
            self._json(_D1_SERIES_CACHE["data"]); return
        try:
            import d1_grading
            rows = d1_grading.mark_to_market(from_as_of=False)
            if rows is None:
                self._json({"error": "no D1 return series"})
                return
            # SPY: NS-7 bench cache; VIX: NS-ETF wf_closes (A_T store has neither)
            spy = self._load_overlay(config.BENCH_CACHE, "SPY")
            vix = self._load_overlay(
                Path(__file__).resolve().parent.parent / "NS-ETF_QA" / "data"
                / "wf_closes.json", "^VIX")
            window = rows[-252:]
            payload = {
                "dates": [r["date"] for r in window],
                "returns": [r["return"] for r in window],
                "spy": [spy.get(r["date"]) for r in window],
                "vix": [vix.get(r["date"]) for r in window],
                "estimated": True,   # current-book weights over history
            }
            _D1_SERIES_CACHE["data"], _D1_SERIES_CACHE["ts"] = payload, now
            self._json(payload)
        except Exception as exc:
            self._json({"error": f"D1 series unavailable: {exc}"})

    @staticmethod
    def _load_overlay(path, key):
        """{date: value} from a bench/wf overlay JSON. {} fail-open."""
        import json as _json
        from pathlib import Path as _P
        try:
            doc = _json.loads(_P(path).read_text())
            node = doc.get(key) if isinstance(doc, dict) else None
            if isinstance(node, dict):                 # {date: close}
                return {k: float(v) for k, v in node.items() if v is not None}
            if isinstance(node, list):                 # [[date, close], ...]
                return {row[0]: float(row[1]) for row in node
                        if row and len(row) > 1 and row[1] is not None}
        except Exception:
            pass
        return {}

    def _d1_tenure(self):
        """v4.6: days-on-Major-league for current basket names (badge source)."""
        import d1_basket
        try:
            doc = json.loads(config.D1_BASKET_PATH.read_text())
            self._json({"as_of": doc.get("as_of"),
                        "tenure": d1_basket.tenure_days(list(doc["weights"]))})
        except Exception:
            self._json({"error": "no d1_basket yet"})

    def _league_reason(self, row: dict, facts: dict, major_qual: bool) -> str:
        """Why this ticker is in its league — the drill-down headline."""
        league = row["league"]
        if league == config.LEAGUE_MAJOR:
            if facts.get("in_sp500"):
                return "SP500 index member — automatic Major (no probation)"
            cap = facts.get("market_cap") or 0
            if cap > config.MARKET_CAP_MAJOR_FASTTRACK:
                return "Non-SP500, market cap > $75B — fast-track Major"
            return "Non-SP500 $50-75B — 90-day compliance clock earned Major"
        if league == config.LEAGUE_MINOR:
            nc = int(row.get("consecutive_noncompliant") or 0)
            cc = int(row.get("consecutive_compliant") or 0)
            if nc > 0:
                return f"Below league floor — noncompliance day {nc}/{config.GRACE_PERIOD_DAYS}"
            return (f"Non-SP500 $50-75B — 90-day probation, "
                    f"day {cc}/{config.GRACE_PERIOD_DAYS} compliant")
        return ("Removed — 90 consecutive days out of compliance "
                "(data preserved, re-admits as fresh)")

    def _selection_status(self, ticker: str) -> dict:
        """Where this ticker stands in the latest selection feed."""
        latest = store.latest_selection()
        payload = (latest or {}).get("payload", {})
        scores = payload.get("scores", [])
        selections = payload.get("selections", [])
        rank = next((i + 1 for i, s in enumerate(scores)
                     if s.get("ticker") == ticker), None)
        in_top_n = any(s.get("ticker") == ticker for s in selections)
        return {
            "rank": rank,
            "scored_count": len(scores),
            "in_top_n": in_top_n,
            "band_kept": bool(in_top_n and rank and rank > (payload.get("top_n") or 0)),
            "as_of": (latest or {}).get("as_of"),
        }

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
            major_qual = universe.major_qualifying(facts)
        except Exception as exc:  # noqa: BLE001 — A_T store may be down
            log.warning("facts_for(%s) failed: %s", ticker, exc)
            facts, compliant, major_qual = {}, None, None
        try:
            mom = pipeline.momentum_detail(ticker, as_of)
        except Exception as exc:  # noqa: BLE001
            log.warning("momentum_detail(%s) failed: %s", ticker, exc)
            mom = None
        grace_left = max(
            0, config.GRACE_PERIOD_DAYS - int(row.get("consecutive_compliant", 0)))
        self._json({
            "ticker": ticker,
            "tracked": True,
            "league": row["league"],
            "league_reason": self._league_reason(row, facts, bool(major_qual)),
            "consecutive_compliant": row["consecutive_compliant"],
            "consecutive_noncompliant": row["consecutive_noncompliant"],
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
            "compliant_today": compliant,
            "major_qualifying": major_qual,
            "grace_days_left_to_promotion": grace_left if row["league"] == config.LEAGUE_MINOR else None,
            "ns2_signal": pipeline.load_ns2_signals().get(ticker.upper()),
            "ns2_advisory": pipeline.load_ns2_signals().get(ticker.upper()) in config.NS2_NO_CONVICTION,
            # v4.4: daily badge snapshot (fresh HMM signal + 4-framework value
            # screen) for benchmark outperformers; None when absent/stale.
            "vs": vs_badges.ticker_entry(ticker),
            "selection": self._selection_status(ticker),
            "momentum_window": mom,
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
