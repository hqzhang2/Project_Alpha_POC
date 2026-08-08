#!/usr/bin/env python3
"""
Tests for the Macro page module (QA_terminal/macro.py).

Network-free: FRED fetch and yfinance corr are mocked; the pure logic
(catalog shape, spread/qoq computation, fail-open, cache, R2 wiring) is
exercised without hitting the network. The route handler is tested against
an in-process server like test_year_highs.py.
"""
import json
import os
import sys
import threading
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import macro


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    """Module-level _cache leaks across tests — give each test a clean one."""
    monkeypatch.setattr(macro, "_cache", {})


# --------------------------------------------------------------------------- #
# Catalog
# --------------------------------------------------------------------------- #
def test_catalog_six_groups():
    keys = [g["key"] for g in macro.GROUPS]
    assert keys == ["growth", "inflation", "monetary", "credit", "external", "markets"]
    names = [g["name"] for g in macro.GROUPS]
    assert "Growth & Labor" in names and "Markets" in names


def test_catalog_all_items_have_required_fields():
    for g in macro.GROUPS:
        for it in g["items"]:
            assert it["id"] and it["name"] and it["cadence"]
            assert "unit" in it
    # every item resolvable by id
    assert len(macro.SERIES_BY_ID) == sum(len(g["items"]) for g in macro.GROUPS)


def test_catalog_no_dead_series():
    """Regression: ISM PMI (NAPMPMI) was removed from FRED (2016) — Hong
    approved dropping the card; no dead FRED ids may be in the catalog."""
    for g in macro.GROUPS:
        for it in g["items"]:
            assert it["id"] != "NAPMPMI"


# --------------------------------------------------------------------------- #
# Pure computations (no network)
# --------------------------------------------------------------------------- #
def test_qoq_ann():
    obs = [{"date": "2026-01-01", "value": 100.0},
           {"date": "2026-04-01", "value": 110.0}]
    out = macro._qoq_ann(obs)
    assert len(out) == 1
    # (1.10^4 - 1) * 100 = 46.41
    assert out[0]["date"] == "2026-04-01"
    assert abs(out[0]["value"] - 46.41) < 0.01


def test_qoq_ann_skips_missing():
    obs = [{"date": "2026-01-01", "value": 100.0},
           {"date": "2026-04-01", "value": 0.0},
           {"date": "2026-07-01", "value": 110.0}]
    out = macro._qoq_ann(obs)
    assert out == []  # both pairs touch a zero value -> skipped (defensive)


def test_spread_aligns_by_date(monkeypatch):
    a = [{"date": "2026-01-02", "value": 4.5}, {"date": "2026-01-03", "value": 4.6}]
    b = [{"date": "2026-01-02", "value": 4.0}]  # missing 01-03 -> pair dropped
    monkeypatch.setattr(macro, "get_series", lambda sid: a if sid == "A" else b)
    out = macro._spread("A", "B", scale=100)
    assert out == [{"date": "2026-01-02", "value": 50.0}]


def test_item_payload_unit_scale(monkeypatch):
    monkeypatch.setattr(macro, "get_series", lambda sid: [
        {"date": "2026-01-02", "value": 199000.0}])
    item = {"id": "ICSA", "unit": "K", "cadence": "Weekly", "unit_scale": 0.001}
    assert macro._item_payload(item) == [{"date": "2026-01-02", "value": 199.0}]


# --------------------------------------------------------------------------- #
# Fail-open
# --------------------------------------------------------------------------- #
def test_fail_open_no_key(monkeypatch):
    monkeypatch.delenv(macro.FRED_API_KEY_ENV, raising=False)
    assert macro._observations("UNRATE", "2026-01-01") == []
    payload = macro.get_macro()
    assert payload["configured"] is False
    assert len(payload["groups"]) == 6


def test_fail_open_fetch_error(monkeypatch):
    monkeypatch.setenv(macro.FRED_API_KEY_ENV, "x" * 32)

    def boom(*a, **k):
        raise OSError("network down")
    # patch the transport, keep the real _observations try/except -> fail-open []
    monkeypatch.setattr(macro.urllib.request, "urlopen", boom)
    assert macro._observations("UNRATE", "2026-01-01") == []
    assert macro.get_series("UNRATE") == []


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
def test_cache_returns_cached(monkeypatch):
    monkeypatch.setenv(macro.FRED_API_KEY_ENV, "x" * 32)
    calls = []
    monkeypatch.setattr(macro, "_observations",
                        lambda sid, start, units=None: calls.append(sid) or
                        [{"date": "2026-01-01", "value": 1.0}])
    macro.get_series("UNRATE")
    macro.get_series("UNRATE")
    assert calls.count("UNRATE") == 1  # second call served from cache


# --------------------------------------------------------------------------- #
# R2 wiring (in-process server)
# --------------------------------------------------------------------------- #
@pytest.fixture
def server(tmp_path, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    import importlib

    import server as srv
    importlib.reload(srv)
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


def test_route_macro_registered(server, monkeypatch):
    monkeypatch.delenv(macro.FRED_API_KEY_ENV, raising=False)
    data = _get_json(server, "/api/macro")
    assert data["configured"] is False
    keys = [g["key"] for g in data["groups"]]
    assert keys == ["growth", "inflation", "monetary", "credit", "external", "markets"]
    # fail-open: every item present with empty observations
    for g in data["groups"]:
        for it in g["items"]:
            assert "observations" in it
