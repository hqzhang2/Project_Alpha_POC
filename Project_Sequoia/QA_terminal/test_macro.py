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
import time
import urllib.request
from datetime import date, datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import macro


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    """Module-level _cache leaks across tests — give each test a clean one.
    Also disable the background pre-warm daemon: it would otherwise start on
    the first get_macro() call and race the per-test _cache reset."""
    monkeypatch.setattr(macro, "_cache", {})
    monkeypatch.setattr(macro, "_prewarm_enabled", False)


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
# Parallel cold-fill + background pre-warm (2026-08-10)
# --------------------------------------------------------------------------- #
def test_all_series_keys_covers_catalog_and_tenors():
    keys = set(macro._all_series_keys())
    # every catalog item (raw or computed-from) is represented
    for g in macro.GROUPS:
        for it in g["items"]:
            if it.get("computed") == "corr":
                # SPY/TLT are Yahoo tickers — must never be FRED cache keys
                for sid in it.get("from", []):
                    assert sid + ":" not in keys
                continue
            for sid in it.get("from", [it["id"]]):
                assert sid + ":" + it.get("units", "") in keys
    # every yield-curve tenor is represented
    for _label, sid, _years in macro.TENORS:
        assert sid + ":" in keys
    # no duplicates (set) and no empty keys
    assert len(keys) == len(macro._all_series_keys())


def test_prefetch_fetches_only_stale_in_parallel(monkeypatch):
    """Cold entries are fetched exactly once each; warm entries untouched."""
    fetched = []
    monkeypatch.setattr(macro, "_observations",
                        lambda sid, start, units=None: fetched.append(sid) or
                        [{"date": "2026-01-01", "value": 1.0}])
    monkeypatch.setattr(macro, "_corr_60d", lambda: [])
    monkeypatch.setattr(macro, "_yahoo_yield_curve", lambda: None)
    all_sids = sorted({k.split(":")[0] for k in macro._all_series_keys()})

    macro._prefetch_missing()
    assert sorted(set(fetched)) == all_sids      # every series fetched
    assert len(fetched) == len(all_sids)         # exactly once each (no dupes)

    # second call: everything warm (real get_series cached it) -> no fetches
    fetched.clear()
    macro._prefetch_missing()
    assert fetched == []


def test_prefetch_skips_fresh_entries(monkeypatch):
    """A series whose cache entry is still inside its TTL is not re-fetched."""
    fetched = []
    monkeypatch.setattr(macro, "_observations",
                        lambda sid, start, units=None: fetched.append(sid) or
                        [{"date": "2026-01-01", "value": 1.0}])
    monkeypatch.setattr(macro, "_corr_60d", lambda: [])
    monkeypatch.setattr(macro, "_yahoo_yield_curve", lambda: None)
    # seed a fresh (just-now) entry for UNRATE — must be skipped
    macro._cache["UNRATE:"] = (time.time(), [{"date": "2026-01-01", "value": 3.9}])

    macro._prefetch_missing()
    assert "UNRATE" not in fetched


def test_prefetch_fail_open(monkeypatch):
    """A raising fetcher must not take down the request (fail-open)."""
    def boom(sid):
        raise OSError("network down")
    monkeypatch.setattr(macro, "get_series", boom)
    monkeypatch.setattr(macro, "_corr_60d", lambda: [])
    monkeypatch.setattr(macro, "_yahoo_yield_curve", lambda: None)
    macro._prefetch_missing()   # must not raise


def test_start_prewarm_disabled_by_default_in_tests():
    """Autouse fixture sets _prewarm_enabled=False -> start_prewarm is a no-op
    and no daemon thread is ever spawned inside the test process."""
    before = [t.name for t in threading.enumerate()]
    macro.start_prewarm()
    after = [t.name for t in threading.enumerate()]
    assert "macro-prewarm" not in after
    assert before == after
    assert macro._prewarm_started is False


def test_start_prewarm_starts_once_and_stops(monkeypatch):
    """Enabled: one daemon thread, idempotent, stoppable via _prewarm_stop."""
    monkeypatch.setattr(macro, "_prewarm_enabled", True)
    monkeypatch.setattr(macro, "_prewarm_started", False)
    macro._prewarm_stop.clear()
    macro.start_prewarm()
    macro.start_prewarm()  # idempotent — no second thread
    threads = [t for t in threading.enumerate() if t.name == "macro-prewarm"]
    assert len(threads) == 1
    macro._prewarm_stop.set()
    threads[0].join(timeout=5)
    assert not threads[0].is_alive()


# --------------------------------------------------------------------------- #
# Treasury yield curve (network-free)
# --------------------------------------------------------------------------- #
def test_weekday_rules():
    assert macro._last_weekday(date(2026, 8, 8)) == date(2026, 8, 7)   # Sat -> Fri
    assert macro._last_weekday(date(2026, 8, 9)) == date(2026, 8, 7)   # Sun -> Fri
    assert macro._last_weekday(date(2026, 8, 10)) == date(2026, 8, 10)  # Mon stays
    assert macro._last_weekday(date(2026, 8, 11)) == date(2026, 8, 11)  # Tue stays


def test_month_offset_clamps():
    assert macro._month_offset(date(2026, 1, 31), 1) == date(2025, 12, 31)
    assert macro._month_offset(date(2026, 3, 31), 1) == date(2026, 2, 28)  # non-leap
    assert macro._month_offset(date(2026, 8, 15), 3) == date(2026, 5, 15)
    assert macro._month_offset(date(2026, 8, 15), 24) == date(2024, 8, 15)


def test_fall_backward():
    days = [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5), date(2026, 8, 6), date(2026, 8, 7)]
    assert macro._fall_backward(date(2026, 8, 8), days) == date(2026, 8, 7)     # weekend -> Fri
    assert macro._fall_backward(date(2026, 8, 7), days[:-1]) == date(2026, 8, 6)  # holiday Fri -> Thu
    assert macro._fall_backward(date(2026, 8, 5), days) == date(2026, 8, 5)     # exact hit
    assert macro._fall_backward(date(2026, 7, 1), days) is None                  # before data


def test_curve_on_accepts_date(monkeypatch):
    obs = {"DGS1MO": [{"date": "2026-08-06", "value": 3.8}],
           "DGS30": [{"date": "2026-08-06", "value": 5.2}]}
    monkeypatch.setattr(macro, "get_series", lambda sid: obs.get(sid, []))
    maps = macro._tenor_maps()
    c = macro._curve_on(date(2026, 8, 6), maps)
    assert c and c["date"] == "2026-08-06" and len(c["points"]) == 2
    assert macro._curve_on(date(2026, 8, 5), maps) is None


def _fake_dgs(monkeypatch, days_map):
    """Wire a fake get_series: sid -> {date: value} for yield-curve tests."""
    def get_series(sid, _orig=macro.get_series):
        return [{"date": d.isoformat(), "value": v} for d, v in days_map.get(sid, [])]
    monkeypatch.setattr(macro, "get_series", get_series)


def _fake_yahoo(monkeypatch, curve):
    """Wire _yahoo_yield_curve to a fixed payload (None = Yahoo unavailable)."""
    monkeypatch.setattr(macro, "_yahoo_yield_curve", lambda: curve)


def _trading_days_around(end, n, skip=None):
    """n weekdays ending at `end`, minus any skip dates (holidays)."""
    days, d = [], end
    while len(days) < n:
        if d.weekday() < 5 and d not in (skip or set()):
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


def test_yield_curve_today_omitted_when_no_data(monkeypatch):
    # today = Sat 2026-08-08 (eff Fri 08-07); FRED data ends 08-06 -> today
    # FALLS BACK to 08-06 (last available; Hong 2026-08-10 — fall-back, not
    # omit, so a normal trading day with a FRED publication lag isn't shown
    # as missing). yesterday = last weekday before resolved today = 08-05.
    days = _trading_days_around(date(2026, 8, 6), 750)
    days_map = {sid: [(d, 4.0 + (d.day % 5) * 0.1) for d in days] for _, sid, _ in macro.TENORS}
    _fake_dgs(monkeypatch, days_map)

    class FakeNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 8, 12, 0, 0, tzinfo=tz)
    monkeypatch.setattr(macro, "datetime", FakeNow)
    _fake_yahoo(monkeypatch, None)  # network-free: Yahoo unavailable

    yc = macro.get_yield_curve()
    assert yc["curves"]["today"]["date"] == "2026-08-06"       # fall-back to last available
    assert yc["curves"]["yesterday"]["date"] == "2026-08-05"   # weekday before resolved today
    for k in ("1W", "1M", "3M", "6M", "1Y", "2Y"):
        assert k in yc["curves"]                            # fall backward always finds one
    assert yc["curves"]["YTD"]["date"].startswith("2026-")  # first of year
    assert yc["tenors"] == ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"]


def test_yield_curve_yahoo_live_override_weekday(monkeypatch):
    # Monday 2026-08-10; FRED data ends Fri 08-07; Yahoo has TODAY (08-10)
    # -> today = Yahoo live curve (source yahoo, 4 tenors), yesterday = FRED.
    days = _trading_days_around(date(2026, 8, 7), 400)
    days_map = {sid: [(d, 4.0) for d in days] for _, sid, _ in macro.TENORS}
    _fake_dgs(monkeypatch, days_map)
    _fake_yahoo(monkeypatch, {"date": "2026-08-10", "source": "yahoo",
                              "points": [{"tenor": "3M", "years": 0.25, "yield": 3.7},
                                         {"tenor": "5Y", "years": 5.0, "yield": 4.4},
                                         {"tenor": "10Y", "years": 10.0, "yield": 4.7},
                                         {"tenor": "30Y", "years": 30.0, "yield": 5.2}]})

    class FakeNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 10, 12, 0, 0, tzinfo=tz)
    monkeypatch.setattr(macro, "datetime", FakeNow)

    yc = macro.get_yield_curve()
    assert yc["curves"]["today"]["date"] == "2026-08-10"        # Yahoo wins (fresher)
    assert yc["curves"]["today"]["source"] == "yahoo"
    assert len(yc["curves"]["today"]["points"]) == 4            # 3M/5Y/10Y/30Y only
    assert yc["curves"]["yesterday"]["date"] == "2026-08-07"    # last available before effective today (08-10)


def test_yield_curve_yahoo_no_override_on_weekend(monkeypatch):
    # Sunday 2026-08-09: FRED resolved = Fri 08-07; Yahoo last close is ALSO
    # Fri 08-07 (no weekend trading) -> equal dates, NO override (FRED today).
    days = _trading_days_around(date(2026, 8, 7), 400)
    days_map = {sid: [(d, 4.0) for d in days] for _, sid, _ in macro.TENORS}
    _fake_dgs(monkeypatch, days_map)
    _fake_yahoo(monkeypatch, {"date": "2026-08-07", "source": "yahoo",
                              "points": [{"tenor": "10Y", "years": 10.0, "yield": 4.7}]})

    class FakeNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 9, 12, 0, 0, tzinfo=tz)
    monkeypatch.setattr(macro, "datetime", FakeNow)

    yc = macro.get_yield_curve()
    assert "source" not in yc["curves"]["today"]                # FRED curve kept
    assert yc["curves"]["today"]["date"] == "2026-08-07"
    assert yc["curves"]["today"]["points"][0]["tenor"] == "1M"  # full FRED ladder


def test_yield_curve_yahoo_loses_when_fred_caught_up(monkeypatch):
    # Monday 2026-08-10, but FRED ALREADY published today (08-10): Yahoo date
    # == FRED resolved date -> no override, FRED wins (Hong: FRED replaces
    # Yahoo once available).
    days = _trading_days_around(date(2026, 8, 10), 400)
    days_map = {sid: [(d, 4.0) for d in days] for _, sid, _ in macro.TENORS}
    _fake_dgs(monkeypatch, days_map)
    _fake_yahoo(monkeypatch, {"date": "2026-08-10", "source": "yahoo",
                              "points": [{"tenor": "10Y", "years": 10.0, "yield": 4.7}]})

    class FakeNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 10, 18, 30, 0, tzinfo=tz)  # after FRED's ~6pm publish
    monkeypatch.setattr(macro, "datetime", FakeNow)

    yc = macro.get_yield_curve()
    assert "source" not in yc["curves"]["today"]                # FRED today (08-10)
    assert yc["curves"]["today"]["date"] == "2026-08-10"
    assert len(yc["curves"]["today"]["points"]) == 11           # full FRED ladder


def test_yield_curve_yesterday_falls_back_on_friday_holiday(monkeypatch):
    # today = Mon 2026-08-10; Fri 08-07 is a holiday (no data). yesterday now
    # falls back to the last available curve before the effective today
    # (08-06), never omitted — consistent with today's fall-back, not omit rule.
    days = _trading_days_around(date(2026, 8, 10), 400, skip={date(2026, 8, 7)})
    days_map = {sid: [(d, 4.0) for d in days] for _, sid, _ in macro.TENORS}
    _fake_dgs(monkeypatch, days_map)

    class FakeNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 10, 12, 0, 0, tzinfo=tz)
    monkeypatch.setattr(macro, "datetime", FakeNow)
    _fake_yahoo(monkeypatch, None)  # network-free: Yahoo unavailable

    yc = macro.get_yield_curve()
    assert yc["curves"]["today"]["date"] == "2026-08-10"
    assert yc["curves"]["yesterday"]["date"] == "2026-08-06"   # falls back past the Fri holiday


def test_yield_curve_yesterday_follows_yahoo_today(monkeypatch):
    # Tuesday 2026-08-11; FRED ends Fri 08-07; Yahoo has TODAY (08-11) ->
    # today = Yahoo (08-11); yesterday = last available curve before the
    # effective today = 08-07 (NOT 08-06, the old FRED-resolved weekday).
    # 1W = 08-04. Regression for the reported "yesterday stuck at 08-06"
    # when today is Yahoo-overridden to a fresher date.
    days = _trading_days_around(date(2026, 8, 7), 400)
    days_map = {sid: [(d, 4.0) for d in days] for _, sid, _ in macro.TENORS}
    _fake_dgs(monkeypatch, days_map)
    _fake_yahoo(monkeypatch, {"date": "2026-08-11", "source": "yahoo",
                              "points": [{"tenor": "10Y", "years": 10.0, "yield": 4.7}]})

    class FakeNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 11, 12, 0, 0, tzinfo=tz)
    monkeypatch.setattr(macro, "datetime", FakeNow)

    yc = macro.get_yield_curve()
    assert yc["curves"]["today"]["date"] == "2026-08-11"
    assert yc["curves"]["today"]["source"] == "yahoo"
    assert yc["curves"]["yesterday"]["date"] == "2026-08-07"
    assert yc["curves"]["1W"]["date"] == "2026-08-04"


def test_corr_60d_cached(monkeypatch):
    """P1 refactor: stock-bond corr is TTL-cached — second call must not
    re-download from Yahoo (one download per Daily TTL window)."""
    import numpy as np
    import pandas as pd
    calls = []

    class FakeYf:
        @staticmethod
        def download(*a, **k):
            calls.append(1)
            idx = pd.date_range("2024-08-01", periods=200, freq="B")
            rng = np.random.default_rng(0)
            close = pd.DataFrame({"SPY": 100 + np.cumsum(rng.normal(0, 1, 200)),
                                  "TLT": 100 + np.cumsum(rng.normal(0, 1, 200))}, index=idx)
            return pd.concat({"Close": close}, axis=1)  # yfinance MultiIndex shape

    monkeypatch.setitem(sys.modules, "yfinance", FakeYf())
    out1 = macro._corr_60d()
    out2 = macro._corr_60d()
    assert len(calls) == 1          # second call served from cache
    assert out1 and out1 == out2    # same payload, non-empty


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
