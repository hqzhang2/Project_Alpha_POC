"""G1 tests — live circuit breakers & position stops in the enforcement loop.

Hermetic: temp DB (store.DB_PATH monkeypatched), no regime store (latest ->
None), no live network. Covers the sign-bug guard (a REAL breach AND a real
non-breach), the per-ticker drawdown/correlation helpers, the asset-class
map, the persist-once dedupe, and re-entry hysteresis surfaced as
`reentry_blocked`.

Run: env -u PYTHONPATH python3 -m pytest tests/test_g1_live_enforcement.py -q
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

import config
import price_feed
import qa_server
import store


# ── Fixtures / helpers ────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "ns6.db")
    store.init_db()
    # Hermetic: no regime-store read, no live NS-5 portfolio file.
    monkeypatch.setattr(qa_server.regime_store_mod, "latest", lambda: None)
    monkeypatch.setattr(qa_server, "NS5_PORTFOLIOS_PATH", tmp_path / "nope.json")
    # Hermetic (G3): no options-chain network fetch -> proxy fallback.
    monkeypatch.setattr(qa_server.options_feed_mod, "live_premiums",
                        lambda **kw: {"put_frac": None, "call_frac": None, "source": "proxy"})
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


def _closes(*series, index=None):
    idx = index or pd.date_range("2026-06-01", periods=len(series[0][1]), freq="B")
    out = {}
    for ticker, vals in series:
        out[ticker] = pd.Series(vals, index=idx, dtype=float, name="Close")
    return out


def _status(monkeypatch, portfolio_dd=-0.02, budget=-0.05, **rowkw):
    """Persist a fresh drawdown row and run _enforcement_status."""
    store.set_active_profile("balanced")
    fresh = (date.today() - timedelta(days=1)).isoformat()
    store.upsert_drawdown(fresh, -0.10, portfolio_dd, budget, 0.6, 0.6, **rowkw)
    h = _make_handler()
    h._enforcement_status()
    return h._sent["body"]


# ── Pure helpers (price_feed) ─────────────────────────────────────────────
def test_compute_position_drawdowns():
    closes = _closes(
        ("MSFT", [100.0, 100.0, 100.0]),
        ("NVDA", [200.0, 100.0, 100.0]),  # down 50% from peak
    )
    dd = price_feed.compute_position_drawdowns(closes)
    assert dd["MSFT"] == pytest.approx(0.0)
    assert dd["NVDA"] == pytest.approx(-0.50)


def test_cross_sectional_corr_identical_series():
    closes = _closes(
        ("A", [100.0, 110.0, 120.0, 130.0]),
        ("B", [50.0, 55.0, 60.0, 65.0]),  # same returns as A -> corr 1.0
    )
    assert price_feed.cross_sectional_corr(closes) == pytest.approx(1.0)


def test_cross_sectional_corr_opposite_series():
    # Varying daily returns, exactly opposite -> corr -1.0. (Constant returns
    # would make correlation undefined — NaN — so the fixtures must vary.)
    closes = _closes(
        ("A", [100.0, 110.0, 115.5]),
        ("B", [100.0, 90.0, 85.5]),  # mirror returns -> corr -1.0
    )
    assert price_feed.cross_sectional_corr(closes) == pytest.approx(-1.0)


def test_cross_sectional_corr_fail_open():
    # single ticker -> None (can't correlate)
    closes = _closes(("A", [100.0, 110.0, 121.0]))
    assert price_feed.cross_sectional_corr(closes) is None
    assert price_feed.cross_sectional_corr({}) is None


# ── Asset-class map (config) ──────────────────────────────────────────────
def test_asset_class_map():
    assert config.asset_class("BIL") == "cash_proxy"
    assert config.asset_class("SPY") == "equity"
    assert config.asset_class("TLT") == "bond_etf"
    assert config.asset_class("GLD") == "commodity_etf"
    assert config.asset_class("IEF") == "bond_etf"
    assert config.asset_class("DBC") == "commodity_etf"
    # everything else -> equity (default), case-insensitive
    assert config.asset_class("AAPL") == "equity"
    assert config.asset_class("msft") == "equity"


def test_asset_class_corr_lookback_in_theta():
    assert config.THETA_DEFAULTS["circuit_breakers"]["systemic_event"]["corr_lookback_days"] == 60


# ── store breaker-time helpers ────────────────────────────────────────────
def test_last_breaker_time_excludes_stops(tmp_path, monkeypatch):
    store.log_circuit_breaker("position_stop", "AAPL", "AAPL -25%")
    assert store.last_breaker_time() is None  # stops don't count as breakers
    store.log_circuit_breaker("hard_floor", None, "dd 90% of budget")
    assert store.last_breaker_time() is not None
    assert store.last_stop_times() == {"AAPL": store.last_stop_times()["AAPL"]}


# ── _enforcement_status wiring ────────────────────────────────────────────
def test_hard_floor_real_breach_surfaces_and_persists(monkeypatch):
    """REAL breach: current_dd -0.15 vs budget -0.05 -> hard floor fires."""
    body = _status(monkeypatch, portfolio_dd=-0.15, budget=-0.05,
                   position_drawdowns={"AAPL": -0.10}, cross_sectional_corr=None)
    types = [b["breaker_type"] for b in body["circuit_breakers"]]
    assert "hard_floor" in types
    assert body["position_stops_triggered"] == []  # -0.10 is above the -0.25 stop
    logs = store.query_breakers()
    assert len([r for r in logs if r["breaker_type"] == "hard_floor"]) == 1
    assert body["last_breaker_time"] is not None


def test_clean_non_breach_empty_lists(monkeypatch):
    """REAL non-breach: nothing fires (the sign-bug guard's clean case)."""
    body = _status(monkeypatch, portfolio_dd=-0.01, budget=-0.05,
                   position_drawdowns={"AAPL": -0.05}, cross_sectional_corr=None)
    assert body["circuit_breakers"] == []
    assert body["position_stops_triggered"] == []
    assert body["last_breaker_time"] is None
    assert body["reentry_blocked"] is False
    assert store.query_breakers() == []


def test_systemic_event_fires(monkeypatch):
    """≥60% positions < -15% AND corr > 0.7 -> systemic breaker fires."""
    pos = {"A": -0.20, "B": -0.20, "C": -0.20}
    body = _status(monkeypatch, portfolio_dd=-0.01, budget=-0.05,
                   position_drawdowns=pos, cross_sectional_corr=0.8)
    types = [b["breaker_type"] for b in body["circuit_breakers"]]
    assert "systemic_event" in types
    assert "hard_floor" not in types  # -0.01 vs -0.05 floor doesn't fire
    assert any(r["breaker_type"] == "systemic_event" for r in store.query_breakers())


def test_systemic_event_no_fire_low_corr(monkeypatch):
    pos = {"A": -0.20, "B": -0.20, "C": -0.20}
    body = _status(monkeypatch, portfolio_dd=-0.01, budget=-0.05,
                   position_drawdowns=pos, cross_sectional_corr=0.2)
    assert body["circuit_breakers"] == []  # corr too low -> no systemic


def test_position_stop_fires(monkeypatch):
    """A position down ≥25% from running peak triggers its stop."""
    pos = {"AAPL": -0.30}
    body = _status(monkeypatch, portfolio_dd=-0.01, budget=-0.05,
                   position_drawdowns=pos, cross_sectional_corr=None)
    tickers = [s["ticker"] for s in body["position_stops_triggered"]]
    assert tickers == ["AAPL"]
    assert body["position_stops_triggered"][0]["asset_class"] == "equity"
    assert any(r["breaker_type"] == "position_stop" and r["ticker"] == "AAPL"
               for r in store.query_breakers())


def test_position_stop_no_fire_above_threshold(monkeypatch):
    pos = {"AAPL": -0.20}  # above equity stop -0.25
    body = _status(monkeypatch, portfolio_dd=-0.01, budget=-0.05,
                   position_drawdowns=pos, cross_sectional_corr=None)
    assert body["position_stops_triggered"] == []


def test_reentry_blocked_after_breaker(monkeypatch):
    """A recent breaker within the 5-day window -> reentry_blocked True."""
    store.log_circuit_breaker("hard_floor", None, "dd 90% of budget")
    body = _status(monkeypatch, portfolio_dd=-0.01, budget=-0.05,
                   position_drawdowns={"AAPL": -0.05}, cross_sectional_corr=None)
    assert body["reentry_blocked"] is True


def test_breaker_persisted_exactly_once(monkeypatch):
    """Repeated polls of a still-triggered breaker do NOT duplicate the log."""
    for _ in range(3):
        _status(monkeypatch, portfolio_dd=-0.15, budget=-0.05,
                position_drawdowns={"AAPL": -0.10}, cross_sectional_corr=None)
    logs = store.query_breakers()
    assert len([r for r in logs if r["breaker_type"] == "hard_floor"]) == 1


def test_no_data_fail_open_no_crash(monkeypatch):
    """No price-feed row -> breakers/stops fail-open (empty), no crash."""
    store.set_active_profile("balanced")
    h = _make_handler()
    h._enforcement_status()
    body = h._sent["body"]
    assert body["data_stale"] is True
    assert body["circuit_breakers"] == []
    assert body["position_stops_triggered"] == []
    assert body["reentry_blocked"] is False
    assert store.query_breakers() == []  # nothing persisted without real data
