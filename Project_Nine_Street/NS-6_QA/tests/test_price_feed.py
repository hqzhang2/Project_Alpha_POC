#!/usr/bin/env python3
"""NS-6 R2a price-feed tests (hermetic — no live network / stores).

Covers: holdings resolution (model + NS-5 shares→weights), portfolio NAV
drawdown (shares + weights paths), the sign-correct budget snapshot (real
breach AND real non-breach — a "clean" case alone passes silently), staleness,
and the run_once batch (no-data -> no row; data -> one row).

Run: env -u PYTHONPATH python3 -m pytest tests/test_price_feed.py -q
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

import budget as budget_mod
import config
import price_feed
import store


# ── Fixtures ───────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Isolated NS-6 store (temp db) — price_feed touches no other state."""
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "ns6.db")
    store.init_db()
    monkeypatch.setattr(price_feed, "PRICES_CACHE", tmp_path / "ns6_prices.pkl")
    return tmp_path


def _closes(*series, index=None):
    """Build {ticker: Series} from column name + list of floats, with a
    real DatetimeIndex (matches yfinance shape; as_of reads .date())."""
    idx = index or pd.date_range("2026-06-01", periods=len(series[0][1]), freq="B")
    out = {}
    for ticker, vals in series:
        out[ticker] = pd.Series(vals, index=idx, dtype=float, name="Close")
    return out


# ── Holdings resolution ────────────────────────────────────────────────────
def test_resolve_holdings_model():
    src, is_model, w, shares, lots, cash = price_feed.resolve_holdings("balanced", "model", {})
    assert is_model is True and src == "balanced"
    assert abs(sum(w.values()) - 1.0) < 0.01
    assert shares == {} and lots == {} and cash == 0.0


def test_resolve_holdings_ns5_shares_to_weights():
    ns5 = {"Tech": {"MSFT": {"shares": 120.0}, "NVDA": {"shares": 80.0}}}
    src, is_model, w, shares, lots, cash = price_feed.resolve_holdings("balanced", "Tech", ns5)
    assert is_model is False and src == "Tech"
    assert shares == {"MSFT": 120.0, "NVDA": 80.0}
    assert abs(sum(w.values()) - 1.0) < 0.01
    assert w["MSFT"] == pytest.approx(0.6)  # 120/200
    assert w["NVDA"] == pytest.approx(0.4)
    assert lots == {} and cash == 0.0  # no lots/cash in this store


def test_resolve_holdings_missing_name_falls_back_model():
    src, is_model, w, _, _, _ = price_feed.resolve_holdings("balanced", "Gone", {"Tech": {}})
    assert is_model is True and src == "balanced"


# ── Portfolio NAV drawdown (shares path) ───────────────────────────────────
def test_shares_nav_drawdown_exact():
    closes = _closes(
        ("MSFT", [100.0, 100.0, 100.0]),
        ("NVDA", [200.0, 100.0, 100.0]),
    )
    shares = {"MSFT": 2.0, "NVDA": 1.0}
    nav = price_feed._portfolio_nav_series(closes, {}, shares)
    # day0 NAV=2*100+1*200=400 (peak); day1 NAV=2*100+1*100=300
    assert nav[0] == pytest.approx(400.0)
    assert nav[1] == pytest.approx(300.0)
    dd = budget_mod.compute_drawdown(nav)
    assert dd == pytest.approx(-0.25)  # 300/400 - 1


def test_weights_nav_drawdown_negative_on_decline():
    closes = _closes(
        ("A", [100.0, 110.0, 99.0]),
        ("B", [100.0, 105.0, 100.0]),
    )
    weights = {"A": 0.5, "B": 0.5}
    nav = price_feed._portfolio_nav_series(closes, weights, {})
    assert nav[0] == pytest.approx(1.0)
    # day1 r = .5*.10 + .5*.05 = .075 -> 1.075 (peak)
    assert nav[1] == pytest.approx(1.075)
    # day2 r = .5*(-.10) + .5*(-.0476) -> NAV < peak -> negative drawdown
    dd = budget_mod.compute_drawdown(nav)
    assert dd < 0 and dd > -0.2


# ── Sign-correct budget snapshot: real breach AND real non-breach ──────────
def _snapshot(closes, profile="balanced"):
    src, is_model, w, shares, _, _ = price_feed.resolve_holdings(profile, "model", {})
    theta = config.load_profile(profile)[0]
    return price_feed.compute_snapshot(w, shares, closes, theta)


def test_snapshot_real_breach():
    closes = _closes(
        ("MSFT", [100.0, 100.0, 100.0]),
        ("NVDA", [200.0, 100.0, 100.0]),
        ("SPY", [100.0, 90.0, 80.0]),
    )
    snap = _snapshot(closes)
    assert snap["current_drawdown_pct"] == pytest.approx(-0.25)
    assert snap["spy_drawdown_pct"] == pytest.approx(-0.20)
    # balanced: spy_dd_ratio=0.75 -> budget = min(-0.15, -0.05) = -0.15
    assert snap["budget_pct"] == pytest.approx(-0.15)
    # consumed full budget -> 0 remaining -> hard floor exposure
    assert snap["budget_remaining_pct"] == pytest.approx(0.0)
    assert snap["exposure_multiplier"] == pytest.approx(0.30)  # balanced hard_floor


def test_snapshot_real_non_breach():
    closes = _closes(
        ("MSFT", [100.0, 110.0, 120.0]),
        ("NVDA", [200.0, 210.0, 220.0]),
        ("SPY", [100.0, 102.0, 104.0]),
    )
    snap = _snapshot(closes)
    assert snap["current_drawdown_pct"] == pytest.approx(0.0)  # still at peak
    assert snap["budget_remaining_pct"] == pytest.approx(1.0)
    assert snap["exposure_multiplier"] == pytest.approx(1.0)


# ── Staleness ──────────────────────────────────────────────────────────────
def test_is_stale():
    today = date.today()
    fresh = {"date": (today - timedelta(days=1)).isoformat()}
    stale = {"date": (today - timedelta(days=5)).isoformat()}
    assert price_feed.is_stale(fresh, 2) == (False, fresh["date"])
    assert price_feed.is_stale(stale, 2) == (True, stale["date"])
    assert price_feed.is_stale(None, 2) == (True, None)
    assert price_feed.is_stale({"date": "not-a-date"}, 2) == (True, "not-a-date")


# ── Batch entrypoint ───────────────────────────────────────────────────────
def test_run_once_no_data_writes_no_row(monkeypatch):
    monkeypatch.setattr(price_feed, "fetch_prices", lambda *a, **k: {})
    monkeypatch.setattr(price_feed, "current_holdings",
                        lambda: ("balanced", True, {"SPY": 1.0}, {}, {}, 0.0))
    assert price_feed.run_once() is None
    assert store.latest() is None  # no fake row persisted


def test_run_once_writes_one_row(monkeypatch):
    closes = _closes(
        ("MSFT", [100.0, 100.0, 100.0]),
        ("NVDA", [200.0, 100.0, 100.0]),
        ("SPY", [100.0, 90.0, 80.0]),
        ("^VIX", [20.0, 30.0, 40.0]),
    )
    monkeypatch.setattr(price_feed, "fetch_prices", lambda *a, **k: closes)
    monkeypatch.setattr(price_feed, "current_holdings",
                        lambda: ("balanced", True, {"SPY": 1.0}, {}, {}, 0.0))
    snap = price_feed.run_once()
    assert snap is not None
    row = store.latest()
    assert row is not None
    assert row["portfolio_dd_pct"] == pytest.approx(-0.2)  # SPY-only model mirrors SPY decline
    assert row["date"] == snap["as_of"]
    assert snap["latest_vix"] == pytest.approx(40.0)


def test_run_once_persists_vix_and_enters_crisis(monkeypatch):
    """R3: a high-VIX day persists vix_level and flips crisis-mode on."""
    store.set_crisis_mode(False)
    closes = _closes(
        ("SPY", [100.0, 100.0, 100.0]),
        ("^VIX", [20.0, 35.0, 35.0]),  # >= crisis_in (28) -> crisis
    )
    monkeypatch.setattr(price_feed, "fetch_prices", lambda *a, **k: closes)
    monkeypatch.setattr(price_feed, "current_holdings",
                        lambda: ("balanced", True, {"SPY": 1.0}, {}, {}, 0.0))
    snap = price_feed.run_once()
    assert snap["crisis_mode"] is True
    assert snap["fast_derisk_cap"] == pytest.approx(0.20)  # balanced crisis_floor
    row = store.latest()
    assert row["vix_level"] == pytest.approx(35.0)
    assert store.get_crisis_mode() is True  # persisted


def test_run_once_low_vix_leaves_crisis_off(monkeypatch):
    store.set_crisis_mode(False)
    closes = _closes(
        ("SPY", [100.0, 100.0, 100.0]),
        ("^VIX", [15.0, 15.0, 15.0]),  # below crisis_out -> no crisis
    )
    monkeypatch.setattr(price_feed, "fetch_prices", lambda *a, **k: closes)
    monkeypatch.setattr(price_feed, "current_holdings",
                        lambda: ("balanced", True, {"SPY": 1.0}, {}, {}, 0.0))
    snap = price_feed.run_once()
    assert snap["crisis_mode"] is False
    assert store.get_crisis_mode() is False
