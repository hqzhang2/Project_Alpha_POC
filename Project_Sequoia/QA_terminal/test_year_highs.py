#!/usr/bin/env python3
"""
Tests for the 52-week-high feature: db storage/search layer and the
/api/year-highs route + 5pm scheduler idempotency.

Network-free where possible: db.py uses SQLite only. The scan() function is
mocked so we never hit yfinance. The route handler is exercised against a
live in-process server using Handler (via the test HTTP server fixture).
"""
import os
import sys
import json
import time
import threading
import urllib.request
import urllib.error
import sqlite3

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db


SAMPLE_ROWS = [
    {"ticker": "AAPL", "exchange": "NASDAQ", "sector": "Technology",
     "close": 330.0, "high_52w": 333.74, "pct_off": -1.1, "volume": 50000000},
    {"ticker": "BMY", "exchange": "NYSE", "sector": "Healthcare",
     "close": 60.0, "high_52w": 60.0, "pct_off": 0.0, "volume": 8000000},
    {"ticker": "XYZ", "exchange": "NASDAQ", "sector": "Tech",
     "close": 10.0, "high_52w": 20.0, "pct_off": -50.0, "volume": 1000},
]


# --------------------------------------------------------------------------- #
# db.py unit tests (no network)
# --------------------------------------------------------------------------- #
def test_db_store_and_get(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    d = "2026-07-23"
    n = db.store_year_highs(d, SAMPLE_ROWS)
    assert n == 3
    rows = db.get_year_highs(d)
    assert len(rows) == 3
    # sorted by pct_off asc -> XYZ(-50), AAPL(-1.1), BMY(0.0)
    assert [r["ticker"] for r in rows] == ["XYZ", "AAPL", "BMY"]


def test_db_upsert_replaces_date(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    d = "2026-07-23"
    db.store_year_highs(d, SAMPLE_ROWS)
    db.store_year_highs(d, [SAMPLE_ROWS[0]])  # re-store single row
    assert len(db.get_year_highs(d)) == 1


def test_db_search_by_ticker(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    d = "2026-07-23"
    db.store_year_highs(d, SAMPLE_ROWS)
    res = db.search_year_highs(d, "aapl")
    assert len(res) == 1 and res[0]["ticker"] == "AAPL"
    res2 = db.search_year_highs(d, "tech")
    assert len(res2) == 2  # AAPL + XYZ both sector Technology/Tech


def test_db_list_dates(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.store_year_highs("2026-07-22", SAMPLE_ROWS[:1])
    db.store_year_highs("2026-07-23", SAMPLE_ROWS[:1])
    dates = db.list_dates()
    assert dates == ["2026-07-23", "2026-07-22"]


def test_today_est_str_format():
    s = db.today_est_str()
    assert len(s) == 10 and s[4] == "-" and s[7] == "-"


# --------------------------------------------------------------------------- #
# scan_year_highs sources from finviz (Overview) + enriches via yfinance.
# Mock both so no network. Current architecture (R1): year_highs is a thin
# wrapper over snapshot.py — the FINVIZ_AVAILABLE/Overview flags live on the
# SNAPSHOT module, and the enrich hook is year_highs.enrich_yfinance.
# --------------------------------------------------------------------------- #
def test_scan_year_highs_filters_below_threshold(monkeypatch):
    import year_highs as yh
    import snapshot
    import pandas as pd

    class FakeOverview:
        def __init__(self):
            self._exchange = None
        def set_filter(self, signal=None, filters_dict=None):
            self._exchange = (filters_dict or {}).get("Exchange")
        def screener_view(self):
            if self._exchange == "NASDAQ":
                return pd.DataFrame([
                    {"Ticker": "FAKE", "Company": "FakeCo", "Sector": "Tech",
                     "Exchange": "NASDAQ", "Price": 100.0, "Volume": 1000},
                ])
            return pd.DataFrame()

    monkeypatch.setattr(snapshot, "Overview", FakeOverview)
    monkeypatch.setattr(snapshot, "FINVIZ_AVAILABLE", True)

    # Enrichment returns -5% -> stock is below threshold -> excluded
    monkeypatch.setattr(yh, "enrich_yfinance",
                        lambda ticker, price, window=252, agg="max": (-5.0, 100.0))
    rows = yh.scan_year_highs()
    assert rows == [], "ticker 5% below 52w high must be excluded"

    # Enrichment returns 0% (at the high) -> included, NASDAQ from finviz
    monkeypatch.setattr(yh, "enrich_yfinance",
                        lambda ticker, price, window=252, agg="max": (0.0, 100.0))
    rows2 = yh.scan_year_highs()
    assert len(rows2) == 1
    assert rows2[0]["ticker"] == "FAKE"
    assert rows2[0]["exchange"] == "NASDAQ"
    assert rows2[0]["pct_off"] == 0.0


def test_scan_year_highs_finviz_unavailable(monkeypatch):
    """If finvizfinance is missing, scan returns [] gracefully (no crash)."""
    import snapshot
    monkeypatch.setattr(snapshot, "FINVIZ_AVAILABLE", False)
    monkeypatch.setattr(snapshot, "Overview", None)
    rows = snapshot.scan_candidates("New High")
    assert rows == []


# --------------------------------------------------------------------------- #
# Route + scheduler idempotency (in-process server)
# --------------------------------------------------------------------------- #
@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    import importlib
    import server as srv
    importlib.reload(srv)  # ensure fresh module with patched db.DB_PATH
    # The live server discovers module routes in AlphaTerminalServer.start();
    # this fixture bypasses start(), so discover explicitly (else /api/year-*
    # 404s with "API not found").
    srv.Handler._discover_module_routes()
    httpd = srv.HTTPServer(("127.0.0.1", 0), srv.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield httpd
    httpd.shutdown()


def _get_json(server, path):
    port = server.server_address[1]
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
        return json.loads(r.read().decode())


def test_route_year_highs_default(server):
    d = "2026-07-23"
    db.store_year_highs(d, SAMPLE_ROWS)
    data = _get_json(server, "/api/year-highs")
    assert data["date"] == d
    assert data["count"] == 3


def test_route_year_highs_date_and_search(server):
    d = "2026-07-23"
    db.store_year_highs(d, SAMPLE_ROWS)
    by_date = _get_json(server, f"/api/year-highs?date={d}")
    assert by_date["count"] == 3
    cal = _get_json(server, "/api/year-highs?action=calendar")
    assert d in cal["dates"]


def test_scheduler_idempotent_store(tmp_path, monkeypatch):
    """store_today_snapshot must not overwrite an existing same-day snapshot."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    import snapshot
    import year_highs as yh

    # Deterministic date: snapshot_date() rolls back to YESTERDAY before
    # 9:30am ET (pre-market), so the raw test was time-of-day dependent —
    # pre-populated today but looked up yesterday → existed=False before
    # 9:30am. Pin the date so the idempotency contract is what's tested.
    fixed = "2026-08-10"
    monkeypatch.setattr(snapshot, "snapshot_date", lambda: fixed)

    # Pre-populate the pinned snapshot date
    db.store_year_highs(fixed, SAMPLE_ROWS)

    # scan is mocked to return a different set; store should be a no-op
    monkeypatch.setattr(yh, "scan_year_highs", lambda **kw: [SAMPLE_ROWS[1]])
    date_str, count, existed = yh.store_today_snapshot()
    assert existed is True
    assert count == 3  # unchanged
    rows = db.get_year_highs(fixed)
    assert len(rows) == 3  # still the original 3, not overwritten


# --------------------------------------------------------------------------- #
# Trend chart (Variant B, 2026-08): per-date sector counts.
# Filter must match the page's displayed-count filter (pct_off >= 0 / pct_from_low <= 0)
# so the chart TOTAL legend equals the page status count.
# --------------------------------------------------------------------------- #
def test_db_get_sector_trend_highs_filters_below_high(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    db.store_year_highs("2026-07-23", SAMPLE_ROWS)  # AAPL -1.1, BMY 0.0, XYZ -50
    rows = db.get_sector_trend("year_highs", "pct_off", ">=")
    # only BMY is at/near the high (pct_off >= 0)
    assert rows == [{"date": "2026-07-23", "sector": "Healthcare", "count": 1}]


def test_db_get_sector_trend_lows_filters_above_low(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    # lows table: pct_from_low <= 0 means at/near the low
    db.store_year_lows("2026-07-23", [
        {"ticker": "AAPL", "exchange": "NASDAQ", "sector": "Technology",
         "close": 300.0, "low_52w": 300.0, "pct_from_low": 0.0, "volume": 1},
        {"ticker": "BMY", "exchange": "NYSE", "sector": "Healthcare",
         "close": 55.0, "low_52w": 60.0, "pct_from_low": 1.5, "volume": 1},
    ])
    rows = db.get_sector_trend("year_lows", "pct_from_low", "<=")
    assert rows == [{"date": "2026-07-23", "sector": "Technology", "count": 1}]


def test_db_get_sector_trend_multi_date_sorted(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    db.store_year_highs("2026-07-22", [SAMPLE_ROWS[1]])  # Healthcare
    db.store_year_highs("2026-07-23", [SAMPLE_ROWS[0], SAMPLE_ROWS[1]])  # Tech(-1.1 filtered) + Healthcare
    rows = db.get_sector_trend("year_highs", "pct_off", ">=")
    # 07-22: Healthcare 1; 07-23: Healthcare 1 (AAPL below high excluded)
    assert rows == [
        {"date": "2026-07-22", "sector": "Healthcare", "count": 1},
        {"date": "2026-07-23", "sector": "Healthcare", "count": 1},
    ]


def test_db_get_sector_trend_rejects_bad_args():
    with pytest.raises(ValueError):
        db.get_sector_trend("year_mid", "pct_off", ">=")
    with pytest.raises(ValueError):
        db.get_sector_trend("year_highs", "pct_off", "==")


def test_module_get_trend_highs_and_lows(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    db.store_year_highs("2026-07-23", SAMPLE_ROWS)
    import year_highs as yh
    import year_lows as yl
    assert yh.get_trend() == [{"date": "2026-07-23", "sector": "Healthcare", "count": 1}]
    assert yl.get_trend() == []


def test_route_year_highs_trend(server):
    db.store_year_highs("2026-07-23", SAMPLE_ROWS)
    data = _get_json(server, "/api/year-highs/trend")
    assert data["results"] == [{"date": "2026-07-23", "sector": "Healthcare", "count": 1}]


def test_route_year_lows_trend(server):
    db.store_year_lows("2026-07-23", [
        {"ticker": "AAPL", "exchange": "NASDAQ", "sector": "Technology",
         "close": 300.0, "low_52w": 300.0, "pct_from_low": 0.0, "volume": 1},
    ])
    data = _get_json(server, "/api/year-lows/trend")
    assert data["results"] == [{"date": "2026-07-23", "sector": "Technology", "count": 1}]
