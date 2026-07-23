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
# scan_year_highs uses yfinance -> mock it so no network
# --------------------------------------------------------------------------- #
def test_scan_year_highs_filters_below_threshold(monkeypatch):
    import year_highs as yh
    import sys
    import types

    class FakeHist:
        def __init__(self, closes):
            self._c = closes
        def __len__(self):
            return len(self._c)
        @property
        def iloc(self):
            return _Iloc(self._c)
        def tail(self, n):
            return _Tail(self._c, n)
        def dropna(self):
            return self

    class _Iloc:
        def __init__(self, closes): self.closes = closes
        def __getitem__(self, i): return self.closes[i]
        def __len__(self): return len(self.closes)

    class _Tail:
        def __init__(self, closes, n): self.closes = closes[-n:]
        def max(self): return max(self.closes)

    def fake_fetch(ticker):
        # 100-day series; last close 95, 52w high 100 -> pct_off -5% (excluded)
        return FakeHist([100.0] * 50 + [95.0] * 50)

    monkeypatch.setattr(yh, "_fetch_history", fake_fetch)
    # Patch yfinance in sys.modules so the in-function `import yfinance` gets the fake
    fake_yf = types.SimpleNamespace(
        Ticker=lambda t: types.SimpleNamespace(info={"exchange": "NMS", "sector": "X"})
    )
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)

    rows = yh.scan_year_highs(universe=["FAKE"])
    assert rows == [], "ticker 5% below 52w high must be excluded"

    # Now a ticker at the high -> included and classified NASDAQ
    monkeypatch.setattr(yh, "_fetch_history", lambda t: FakeHist([100.0] * 100))
    rows2 = yh.scan_year_highs(universe=["FAKE2"])
    assert len(rows2) == 1 and rows2[0]["exchange"] == "NASDAQ"


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
    import year_highs as yh

    # Pre-populate today's snapshot
    d = db.today_est_str()
    db.store_year_highs(d, SAMPLE_ROWS)

    # scan is mocked to return a different set; store should be a no-op
    monkeypatch.setattr(yh, "scan_year_highs", lambda **kw: [SAMPLE_ROWS[1]])
    date_str, count, existed = yh.store_today_snapshot()
    assert existed is True
    assert count == 3  # unchanged
    rows = db.get_year_highs(d)
    assert len(rows) == 3  # still the original 3, not overwritten
