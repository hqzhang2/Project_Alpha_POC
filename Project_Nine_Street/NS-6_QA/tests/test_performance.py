"""G2 tests — performance scoreboard (trailing metrics, attribution,
reconciliation) + the daily performance persistence path.

Hermetic: temp DB (store.DB_PATH monkeypatched), synthetic NAV/close fixtures,
no network. The 252-return fixture makes annualized == total (n == 252), a
clean non-circular assertion.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

import performance as performance_mod
import price_feed
import qa_server
import store


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "ns6.db")
    store.init_db()
    monkeypatch.setattr(qa_server.regime_store_mod, "latest", lambda: None)
    monkeypatch.setattr(qa_server, "NS5_PORTFOLIOS_PATH", tmp_path / "nope.json")
    return tmp_path


class _FakeWFile:
    def write(self, b):
        pass


def _make_handler():
    h = qa_server.NS6Handler.__new__(qa_server.NS6Handler)
    h.wfile = _FakeWFile()
    h._sent = {}

    def fake_json(obj, status=200):
        h._sent = {"status": status, "body": obj}

    h._json = fake_json
    return h


def _closes(*series):
    idx = pd.date_range("2026-08-01", periods=len(series[0][1]), freq="B")
    return {tk: pd.Series(vals, index=idx, dtype=float, name="Close")
            for tk, vals in series}


# ── Pure metrics ──────────────────────────────────────────────────────────
def test_trailing_metrics_full_year_annualized_equals_total():
    # 253 NAV points, +100% compounding every day (powers of 2 are exact in
    # IEEE754 -> returns are EXACTLY constant -> std 0 -> sharpe fail-open None).
    navs = [2.0 ** i for i in range(253)]
    m = performance_mod.trailing_metrics(navs)
    assert m is not None
    assert m["n_days"] == 252
    assert m["total_return"] == pytest.approx(2.0 ** 252 - 1, rel=1e-6)
    assert m["annualized_return"] == pytest.approx(m["total_return"], rel=1e-6)
    assert m["sharpe"] is None        # constant returns -> std 0 -> fail-open
    assert m["vol"] == 0.0
    assert m["max_drawdown"] == 0.0   # monotonic up


def test_trailing_metrics_window_and_drawdown():
    navs = [100.0, 110.0, 121.0, 108.9, 119.79]  # rets: .10 .10 -.10 .10
    m = performance_mod.trailing_metrics(navs, window=3)  # 3 returns = 4 NAV pts
    assert m["n_days"] == 3
    assert m["total_return"] == pytest.approx(119.79 / 110.0 - 1.0)   # 0.089
    assert m["max_drawdown"] == pytest.approx(-0.10)   # 108.9 vs 121 peak
    # full series: min dd still -0.10, total +19.79%
    m_full = performance_mod.trailing_metrics(navs)
    assert m_full["total_return"] == pytest.approx(119.79 / 100.0 - 1.0)
    assert m_full["max_drawdown"] == pytest.approx(-0.10)


def test_trailing_metrics_fail_open():
    assert performance_mod.trailing_metrics([]) is None
    assert performance_mod.trailing_metrics([1.0]) is None
    assert performance_mod.trailing_metrics([0.0, 1.0]) is None  # non-positive base


def test_attribution_sums_to_total_return():
    navs = [1.0, 1.007, 1.014]  # total return 0.014
    rows = [
        {"date": "2026-08-01", "nav": 1.0, "contributions": {"AAPL": 0.004, "MSFT": 0.003}},
        {"date": "2026-08-02", "nav": 1.007, "contributions": {"AAPL": 0.002, "MSFT": 0.005}},
        {"date": "2026-08-03", "nav": 1.014, "contributions": {"AAPL": 0.0, "MSFT": 0.0}},
    ]
    a = performance_mod.attribution(rows)
    assert a["top"][0]["ticker"] == "MSFT"   # 0.008 > AAPL 0.006
    assert a["sum_contributions"] == pytest.approx(0.014, abs=1e-6)
    assert performance_mod.trailing_metrics(navs)["total_return"] == pytest.approx(0.014)
    # attribution sum ~= total return (within rounding)
    assert abs(a["sum_contributions"] -
               performance_mod.trailing_metrics(navs)["total_return"]) < 1e-3


def test_reconcile_delta_and_divergence():
    live = {"2026-08-01": 1.0, "2026-08-02": 1.01, "2026-08-03": 1.03}
    bt = {"2026-08-01": 1.0, "2026-08-02": 1.005, "2026-08-03": 1.01}
    r = performance_mod.reconcile(live, bt, divergence_pp=5.0)
    assert r is not None
    assert r["live_total_ret"] == pytest.approx(0.03)
    assert r["backtest_total_ret"] == pytest.approx(0.01)
    assert r["delta_pp"] == pytest.approx(2.0)
    assert r["divergent"] is False
    assert r["overlap_days"] == 3
    # divergent when |delta| > threshold
    live_hot = {"2026-08-01": 1.0, "2026-08-02": 1.04, "2026-08-03": 1.08}
    r2 = performance_mod.reconcile(live_hot, bt, divergence_pp=5.0)
    assert r2["delta_pp"] == pytest.approx(7.0)
    assert r2["divergent"] is True


def test_reconcile_fail_open():
    assert performance_mod.reconcile({"a": 1.0}, {"a": 1.0}) is None  # <2 common
    assert performance_mod.reconcile({}, {}) is None


# ── price_feed.compute_performance (synthetic closes) ────────────────────
def test_compute_performance_weights_path():
    closes = _closes(
        ("A", [100.0, 101.0, 102.0]),      # rets .01, ~.009901
        ("B", [50.0, 50.5, 51.51]),        # rets .01, .02
        ("SPY", [100.0, 101.0, 102.0]),
        ("^VIX", [20.0, 21.0, 22.0]),
    )
    perf = price_feed.compute_performance(closes, {"A": 0.6, "B": 0.4}, {}, "2026-08-05")
    assert perf is not None
    # day2 ret = 0.6*0.00990099 + 0.4*0.02 = 0.01394059
    assert perf["ret"] == pytest.approx(0.01394059, rel=1e-5)
    assert perf["spy_ret"] == pytest.approx(102.0 / 101.0 - 1.0)
    assert perf["universe_ret"] == pytest.approx((0.00990099 + 0.02) / 2.0, rel=1e-4)
    # contributions rounded to 6dp, so allow 1e-5 absolute (sum == ret within rounding)
    assert sum(perf["contributions"].values()) == pytest.approx(perf["ret"], abs=1e-5)


def test_compute_performance_fail_open_short_history():
    closes = _closes(("A", [100.0]), ("SPY", [100.0]))
    assert price_feed.compute_performance(closes, {"A": 1.0}, {}, "2026-08-05") is None


# ── store round-trip + endpoint ───────────────────────────────────────────
def test_store_performance_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "ns6.db")
    store.init_db()
    store.upsert_performance("2026-08-05", 1.013, 0.013, spy_ret=0.01,
                             universe_ret=0.011, contributions={"A": 0.008, "B": 0.005})
    rows = store.query_performance()
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-08-05"
    assert rows[0]["nav"] == pytest.approx(1.013)
    assert rows[0]["contributions"] == {"A": 0.008, "B": 0.005}


def test_performance_endpoint_sane(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "ns6.db")
    store.init_db()
    # 3 daily rows (nav path)
    for i, nav in enumerate([1.0, 1.007, 1.014]):
        store.upsert_performance(f"2026-08-0{i+1}", nav, nav - (1.0 if i == 0 else 1.007),
                                 spy_ret=0.005, universe_ret=0.006,
                                 contributions={"AAPL": 0.004, "MSFT": 0.003})
    h = _make_handler()
    h._performance()
    body = h._sent["body"]
    assert body["as_of"] == "2026-08-03"
    assert set(body) >= {"trailing", "excess", "attribution", "reconciliation"}
    assert body["trailing"]["21d"] is not None or body["trailing"]["21d"] == {}
    assert body["attribution"]["sum_contributions"] is not None
    # no baseline file -> reconciliation fail-open None
    assert body["reconciliation"] is None
