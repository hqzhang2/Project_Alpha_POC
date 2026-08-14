"""G4 + G5 tests — lot-level cost basis + cash, and enforcement alerting.

Hermetic: temp DB, temp alerts file, no network.
G4: resolve_holdings extracts lots+cash; NAV includes cash; tax ranking uses
    real lots; no-lots portfolios still work (proxy fallback).
G5: append_alert writes exactly one line per fire; /api/alerts unread badge
    counts rows newer than last-viewed; mark-viewed clears it.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

import price_feed
import qa_server
import store
import tax_context


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


# ── G4: lots + cash ───────────────────────────────────────────────────────
def test_resolve_holdings_extracts_lots_and_cash():
    ns5 = {"Tech": {
        "MSFT": {"shares": 120.0, "lots": [
            {"qty": 50, "basis": 40.0, "acquired": "2023-01-10"},
            {"qty": 70, "basis": 60.0, "acquired": "2024-05-01"},
        ]},
        "cash": 10_000.0,
    }}
    src, is_model, w, shares, lots, cash = price_feed.resolve_holdings("balanced", "Tech", ns5)
    assert is_model is False
    assert shares == {"MSFT": 120.0}
    assert cash == pytest.approx(10_000.0)
    assert lots["MSFT"]["lots"][0]["shares"] == 50
    assert lots["MSFT"]["lots"][0]["cost_per_share"] == 40.0
    assert lots["MSFT"]["lots"][0]["date"] == "2023-01-10"
    assert w["MSFT"] == pytest.approx(1.0)


def test_nav_includes_cash():
    closes = _closes(("MSFT", [100.0, 110.0, 121.0]), ("SPY", [100.0, 100.0, 100.0]))
    perf = price_feed.compute_performance(closes, {}, {"MSFT": 2.0}, "2026-08-05", cash=10_000.0)
    # NAV = 2*121 + 10000 = 10242 ; prev = 2*110 + 10000 = 10220
    assert perf["nav"] == pytest.approx(10_242.0)
    assert perf["ret"] == pytest.approx(10_242.0 / 10_220.0 - 1.0)


def test_tax_select_lots_uses_real_basis():
    tax_lot_data = {"AAPL": {"lots": [
        {"shares": 100, "cost_per_share": 50.0, "date": "2024-01-10"},
    ]}}
    gain, ltcg, stcg, unclassified = tax_context._select_lots(
        "AAPL", 50, tax_lot_data, sell_price=100.0)
    assert gain == pytest.approx((100 - 50) * 50)
    assert ltcg == pytest.approx((100 - 50) * 50)  # held > 365 days
    assert stcg == 0.0 and unclassified is False


def test_tax_select_lots_proxy_fallback_no_lots():
    # No lot data -> entire proceeds taxable, STCG (conservative), no crash.
    gain, ltcg, stcg, unclassified = tax_context._select_lots(
        "AAPL", 50, {}, sell_price=100.0)
    assert gain == pytest.approx(100.0 * 50)
    assert stcg == pytest.approx(100.0 * 50)
    assert unclassified is True


# ── G5: alerting ──────────────────────────────────────────────────────────
def test_append_alert_writes_line(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ALERTS_LOG", tmp_path / "ns6_alerts.log")
    store.append_alert("hard_floor", "current_dd breached")
    store.append_alert("position_stop", "AAPL -25%")
    lines = (tmp_path / "ns6_alerts.log").read_text().strip().splitlines()
    assert len(lines) == 2
    assert "hard_floor current_dd breached" in lines[0]
    assert "position_stop AAPL -25%" in lines[1]


def test_alerts_endpoint_unread_and_mark_viewed(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ALERTS_LOG", tmp_path / "alerts.log")
    store.log_circuit_breaker("hard_floor", None, "dd 90% of budget")
    h = _make_handler()
    h._alerts()
    assert h._sent["body"]["unread_count"] == 1
    h._alerts_view()
    assert h._sent["body"]["ok"] is True
    h._alerts()
    assert h._sent["body"]["unread_count"] == 0  # cleared on view


def test_breaker_fire_alerts_exactly_once(tmp_path, monkeypatch):
    """A still-triggered breaker across repeated polls -> ONE alert line."""
    monkeypatch.setattr(store, "ALERTS_LOG", tmp_path / "alerts.log")
    h = _make_handler()
    for _ in range(3):
        h._log_breaker_once("hard_floor", None, "dd 90% of budget")
    lines = (tmp_path / "alerts.log").read_text().strip().splitlines()
    assert len(lines) == 1  # dedupe (G1) means exactly one alert line
