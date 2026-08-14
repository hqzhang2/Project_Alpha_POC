"""G3 tests — live options chain -> protective put overlay.

Hermetic: mocked chain payloads (no network), temp DB, options_feed
live_premiums mocked in the shared status fixtures (proxy fallback).
Covers: ATM mid extraction (bid/ask, last fallback), live vs proxy
pricing_source, and the status endpoint surfacing the put overlay.
"""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import options as options_mod
import options_feed as options_feed_mod
import qa_server
import store


# ── Mock chain payloads (A_T /api/options shape) ─────────────────────────
def _chain(spot=100.0, put_strikes=None, put_mids=None, call_mids=None):
    def _opts(strikes, mids):
        return [{"strike": k, "bid": m - 0.05, "ask": m + 0.05, "last": m,
                 "delta": 0, "iv": 0.3, "hasQuote": True}
                for k, m in zip(strikes, mids)]

    return {
        "ticker": "SPY", "expiry": "2026-09-18", "spot": spot,
        "calls": _opts(put_strikes or [95.0, 100.0, 105.0],
                       call_mids or [5.5, 2.0, 0.5]),
        "puts": _opts(put_strikes or [95.0, 100.0, 105.0],
                      put_mids or [0.5, 2.0, 5.5]),
    }


def _no_network(monkeypatch):
    monkeypatch.setattr(options_feed_mod, "fetch_chain",
                        lambda *a, **k: {"ticker": "SPY", "error": "no options"})


# ── options_feed ATM mid extraction ──────────────────────────────────────
def test_atm_put_premium_fraction():
    chain = _chain(spot=100.0, put_strikes=[95.0, 100.0, 105.0],
                   put_mids=[0.5, 2.0, 5.5])
    # ATM = strike 100, mid 2.0 -> 2.0/100 = 0.02 (2% of notional)
    assert options_feed_mod.atm_put_premium(chain) == pytest.approx(0.02)


def test_atm_put_premium_mid_vs_last():
    # No bid/ask -> falls back to last trade.
    chain = _chain(spot=100.0)
    for row in chain["puts"]:
        row["bid"] = None
        row["ask"] = None
    assert options_feed_mod.atm_put_premium(chain) == pytest.approx(0.02)


def test_atm_premium_fail_open():
    assert options_feed_mod.atm_put_premium(None) is None
    assert options_feed_mod.atm_put_premium({"spot": 100.0, "puts": []}) is None
    assert options_feed_mod.atm_put_premium({"spot": 0, "puts": []}) is None
    assert options_feed_mod.atm_call_premium(_chain()) == pytest.approx(2.0 / 100.0)


def test_live_premiums_source_live(monkeypatch):
    monkeypatch.setattr(options_feed_mod, "fetch_chain",
                        lambda *a, **k: _chain())
    live = options_feed_mod.live_premiums()
    assert live["source"] == "live"
    assert live["put_frac"] == pytest.approx(0.02)


def test_live_premiums_source_proxy(monkeypatch):
    _no_network(monkeypatch)
    live = options_feed_mod.live_premiums()
    assert live["source"] == "proxy"
    assert live["put_frac"] is None


# ── options.recommend_put_overlay pricing source ─────────────────────────
def test_put_overlay_uses_live_cost(monkeypatch):
    theta = config.load_theta()
    r = options_mod.recommend_put_overlay(0.5, 1_000_000, vix_level=20.0,
                                          theta=theta, live_put_cost_pct=0.025)
    assert r["recommended"] is True
    assert r["pricing_source"] == "live"
    # annual cost = 0.025 * 12 * multiplier(0.5) * coverage(0.5)
    assert r["estimated_annual_cost_pct"] == pytest.approx(0.025 * 12 * 0.5 * 0.5)


def test_put_overlay_proxy_fallback():
    theta = config.load_theta()
    r = options_mod.recommend_put_overlay(0.5, 1_000_000, vix_level=20.0,
                                          theta=theta, live_put_cost_pct=None)
    assert r["pricing_source"] == "proxy"
    # proxy: vix 20 -> 20/3000 = 0.00667 monthly
    assert r["estimated_annual_cost_pct"] == pytest.approx(0.006667 * 12 * 0.5 * 0.5, rel=1e-3)


def test_put_overlay_no_put_still_reports_source():
    theta = config.load_theta()
    r = options_mod.recommend_put_overlay(1.0, 1_000_000, theta=theta)
    assert r["recommended"] is False
    assert r["pricing_source"] == "proxy"


# ── status endpoint surfaces the overlay ─────────────────────────────────
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


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "ns6.db")
    store.init_db()
    monkeypatch.setattr(qa_server.regime_store_mod, "latest", lambda: None)
    monkeypatch.setattr(qa_server, "NS5_PORTFOLIOS_PATH", tmp_path / "nope.json")
    # Hermetic (G3): stub qa_server's reference to options_feed (namespace, so
    # the REAL options_feed.live_premiums stays intact for the direct tests).
    monkeypatch.setattr(qa_server, "options_feed_mod", types.SimpleNamespace(
        live_premiums=lambda **kw: {"put_frac": None, "call_frac": None, "source": "proxy"}))
    return tmp_path


def test_status_protective_puts_proxy_fallback(tmp_path, monkeypatch):
    store.set_active_profile("balanced")
    h = _make_handler()
    h._enforcement_status()
    body = h._sent["body"]
    puts = body["protective_puts"]
    assert puts is not None
    assert puts["pricing_source"] == "proxy"
    assert puts["live_put_monthly_pct"] is None


def test_status_protective_puts_live(tmp_path, monkeypatch):
    monkeypatch.setattr(qa_server.options_feed_mod, "live_premiums",
                        lambda **kw: {"put_frac": 0.02, "call_frac": 0.015,
                                      "source": "live"})
    store.set_active_profile("balanced")
    h = _make_handler()
    h._enforcement_status()
    puts = h._sent["body"]["protective_puts"]
    assert puts["pricing_source"] == "live"
    assert puts["live_put_monthly_pct"] == pytest.approx(2.0)
