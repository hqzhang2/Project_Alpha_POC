"""Tests for NS-7 qa_server.py — endpoint smoke tests.

Starts the real NS7Handler on an ephemeral port in a thread, with the NS-7
store redirected to temp and A_T reads stubbed (the server must not touch the
real A_T store in tests).

Run: python3 -m pytest NS-7_QA/tests/test_server.py -q
"""
import json
import os
import sys
import threading
import urllib.request
from http.server import HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import qa_server
import store


class _Server:
    def __init__(self, handler, port):
        self.httpd = HTTPServer(("127.0.0.1", port), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def get(self, path):
        import urllib.error
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.httpd.server_port}{path}",
                    timeout=5) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _make_server(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path / "ns7")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "ns7" / "ns7.db")
    store.init_db()

    # Stub A_T reads so the server never touches the real store.
    monkeypatch.setattr(qa_server.pipeline, "sp500_current", lambda: ["AAPL"])
    monkeypatch.setattr(qa_server.pipeline, "facts_for",
                        lambda t, as_of, in_sp500, volume_waived=False: {
                            "ticker": t, "in_sp500": in_sp500,
                            "market_cap": 100e9, "eps_ttm": 6.0,
                            "cfo_ttm": 50e9, "avg_daily_volume": 200_000.0,
                            "snapshot_age_days": 30})
    monkeypatch.setattr(qa_server.pipeline, "momentum_detail",
                        lambda t, as_of: {
                            "p_old": 100.0, "p_old_date": "2026-01-01",
                            "p_skip": 112.5, "p_skip_date": "2026-07-01",
                            "momentum": 0.125})
    monkeypatch.setattr(qa_server.pipeline, "load_ns2_signals", lambda: {})
    store.set_meta("last_refresh", "2026-08-01")

    # Seed league + a selection doc.
    store.upsert_league("AAPL", "major", 120, 0, "2026-01-01", "2026-08-01")
    store.upsert_league("MSFT", "minor", 45, 0, "2026-01-01", "2026-08-01")
    store.save_selection("2026-08-01", {
        "as_of": "2026-08-01", "generated_at": "2026-08-01T12:00:00",
        "service": "NS-7", "methodology": "skip-month momentum 126/21",
        "major_count": 1, "scored_count": 1, "top_n": 20,
        "scores": [{"ticker": "AAPL", "momentum": 0.123456}],
        "selections": [{"ticker": "AAPL", "momentum": 0.123456, "rank": 1}],
    })
    return _Server(qa_server.NS7Handler, 0)


def test_health(tmp_path, monkeypatch):
    srv = _make_server(tmp_path, monkeypatch)
    try:
        status, body = srv.get("/health")
        assert status == 200
        assert body["service"] == "NS-7"
        assert body["last_refresh"] == "2026-08-01"
        assert body["status"] == "ok"
    finally:
        srv.close()


def test_universe_counts_and_membership(tmp_path, monkeypatch):
    srv = _make_server(tmp_path, monkeypatch)
    try:
        status, body = srv.get("/api/universe")
        assert status == 200
        assert body["counts"]["major"] == 1
        assert body["counts"]["minor"] == 1
        tickers = {m["ticker"] for m in body["members"]}
        assert tickers == {"AAPL", "MSFT"}
    finally:
        srv.close()


def test_major_scores_ranked(tmp_path, monkeypatch):
    srv = _make_server(tmp_path, monkeypatch)
    try:
        status, body = srv.get("/api/major")
        assert status == 200
        assert body["major_count"] == 1
        assert body["scores"][0]["ticker"] == "AAPL"
        assert body["scores"][0]["momentum"] == 0.123456
    finally:
        srv.close()


def test_select_returns_feed(tmp_path, monkeypatch):
    srv = _make_server(tmp_path, monkeypatch)
    try:
        status, body = srv.get("/api/select")
        assert status == 200
        assert body["selections"][0]["rank"] == 1
        assert body["as_of"] == "2026-08-01"
    finally:
        srv.close()


def test_league_detail(tmp_path, monkeypatch):
    srv = _make_server(tmp_path, monkeypatch)
    try:
        status, body = srv.get("/api/leagues/AAPL")
        assert status == 200
        assert body["league"] == "major"
        assert body["compliant_today"] is True
        assert body["facts"]["market_cap"] == 100e9
        # Drill-down reasons: league qualification + selection status + window.
        assert body["league_reason"] and "SP500" in body["league_reason"]
        assert body["selection"]["in_top_n"] is True
        assert body["selection"]["rank"] == 1
        assert body["selection"]["band_kept"] is False
        assert body["momentum_window"]["momentum"] == 0.125
        assert body["momentum_window"]["p_old_date"] == "2026-01-01"
        assert body["ns2_signal"] is None   # stubbed NS-2 cache (neutral)
        status, body = srv.get("/api/leagues/zzzz")
        assert status == 404
        assert body["tracked"] is False
    finally:
        srv.close()


def test_dashboard_served(tmp_path, monkeypatch):
    srv = _make_server(tmp_path, monkeypatch)
    try:
        import urllib.request as u
        with u.urlopen(f"http://127.0.0.1:{srv.httpd.server_port}/", timeout=5) as r:
            assert r.status == 200
            assert b"NS-7" in r.read()
    finally:
        srv.close()


def test_unknown_route_404(tmp_path, monkeypatch):
    srv = _make_server(tmp_path, monkeypatch)
    try:
        status, body = srv.get("/api/nope")
        assert status == 404
        assert "not found" in body["error"]
    finally:
        srv.close()
