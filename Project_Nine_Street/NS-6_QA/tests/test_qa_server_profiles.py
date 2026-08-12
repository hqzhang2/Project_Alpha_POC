"""Test qa_server.py profile endpoints (GET/POST /api/profile)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import config
import qa_server
import store


class _FakeWFile:
    def write(self, b):  # swallow
        pass


def _make_handler():
    """A bare NS6Handler with stubbed HTTP plumbing, ready to call handlers."""
    h = qa_server.NS6Handler.__new__(qa_server.NS6Handler)
    h.wfile = _FakeWFile()
    h._sent = {}

    def fake_json(obj, status=200):
        h._sent = {"status": status, "body": obj}

    h._json = fake_json
    return h


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    store.init_db()


def test_profile_get_returns_available_and_active():
    h = _make_handler()
    store.set_active_profile("growth")
    h._profile_get()
    assert h._sent["status"] == 200
    body = h._sent["body"]
    assert body["active_profile"] == "growth"
    names = [p["name"] for p in body["available"]]
    assert set(names) == {"growth", "balanced", "capital_preservation"}


def test_profile_set_valid():
    h = _make_handler()
    h._profile_set({"profile": "capital_preservation"})
    assert h._sent["status"] == 200
    assert h._sent["body"]["active_profile"] == "capital_preservation"
    # persisted
    assert store.get_active_profile() == "capital_preservation"


def test_profile_set_invalid_returns_400():
    h = _make_handler()
    h._profile_set({"profile": "nope"})
    assert h._sent["status"] == 400
    assert "error" in h._sent["body"]
    assert store.get_active_profile() == "balanced"  # unchanged


def test_enforcement_status_reports_active_profile():
    h = _make_handler()
    store.set_active_profile("growth")
    h._enforcement_status()
    body = h._sent["body"]
    assert body["active_profile"] == "growth"
    assert "profile_label" in body


def test_enforcement_status_profile_theta_used():
    """The status multiplier should reflect the profile's hard_floor."""
    h = _make_handler()
    # budget_remaining defaults to 1.0 with no stored row.
    # capital_preservation has hard_floor 0.25; growth has 0.50.
    # At budget_remaining 1.0 both clamp to 1.0, so use a stored row near 0.3.
    store.upsert_drawdown("2026-08-11", -0.04, -0.02, -0.05, 0.3, 0.3)
    h._enforcement_status()
    body = h._sent["body"]
    assert body["active_profile"] == store.get_active_profile()
    # multiplier in [hard_floor, 1.0]
    assert 0.25 <= body["exposure_multiplier"] <= 1.0


# ── T4: regime-gated switch suggestion (advisory, never auto) ────────────
def _monkey_regime(monkeypatch, row):
    """Point qa_server's regime_store.latest() at a fake row."""
    monkeypatch.setattr(qa_server.regime_store_mod, "latest", lambda: row)


def _fresh_row(regime="R2", recorded_at="2026-08-11T20:50:14.510224Z"):
    return {"regime": regime, "recorded_at": recorded_at}


def test_suggestion_aligns_with_active(monkeypatch):
    _monkey_regime(monkeypatch, _fresh_row("R1"))
    store.set_active_profile("growth")
    h = _make_handler()
    h._enforcement_status()
    body = h._sent["body"]
    assert body["regime"] == "R1"
    assert body["suggested_profile"] == "growth"
    assert body["suggestion_active"] is False  # already there


def test_suggestion_differs_from_active(monkeypatch):
    _monkey_regime(monkeypatch, _fresh_row("R3"))
    store.set_active_profile("growth")
    h = _make_handler()
    h._enforcement_status()
    body = h._sent["body"]
    assert body["suggested_profile"] == "capital_preservation"
    assert body["suggestion_active"] is True
    assert "R3" in body["suggestion_reason"]


def test_suggestion_pm_can_lean_back_in(monkeypatch):
    # growth regime while PM is defensive → suggest growth
    _monkey_regime(monkeypatch, _fresh_row("R1"))
    store.set_active_profile("capital_preservation")
    h = _make_handler()
    h._enforcement_status()
    body = h._sent["body"]
    assert body["suggested_profile"] == "growth"
    assert body["suggestion_active"] is True


def test_suggestion_no_regime_data(monkeypatch):
    _monkey_regime(monkeypatch, None)
    h = _make_handler()
    h._enforcement_status()
    body = h._sent["body"]
    assert body["suggested_profile"] is None
    assert body["suggestion_active"] is False
    assert body["suggestion_reason"] == "no regime data"
    assert body["regime"] is None  # don't fake "R1" when there's no data


def test_suggestion_stale_regime(monkeypatch):
    # > 45 days old → no suggestion
    _monkey_regime(monkeypatch, _fresh_row("R3", "2026-01-01T00:00:00Z"))
    h = _make_handler()
    h._enforcement_status()
    body = h._sent["body"]
    assert body["suggested_profile"] is None
    assert body["suggestion_active"] is False
    assert "stale" in body["suggestion_reason"]


def test_suggestion_unknown_regime_fails_open(monkeypatch):
    _monkey_regime(monkeypatch, _fresh_row("R9"))
    h = _make_handler()
    h._enforcement_status()  # must not raise
    body = h._sent["body"]
    assert body["suggested_profile"] is None
    assert body["suggestion_active"] is False


def test_suggestion_never_auto_switches(monkeypatch):
    """Setting a suggestion must NEVER mutate the persisted active profile."""
    _monkey_regime(monkeypatch, _fresh_row("R3"))  # suggests capital_preservation
    store.set_active_profile("growth")
    h = _make_handler()
    h._enforcement_status()
    assert store.get_active_profile() == "growth"  # unchanged — advisory only


def test_drift_accepts_current_weights_body():
    """POST /api/drift with a modified current_weights must produce alerts."""
    h = _make_handler()
    # weights differ from DEFAULT_WEIGHTS target → drift alerts expected
    h._drift({"current_weights": {"AAPL": 0.20, "MSFT": 0.05, "TLT": 0.10, "GLD": 0.30}})
    body = h._sent["body"]
    assert "alerts" in body and "summary" in body
    assert len(body["alerts"]) > 0  # drift detected vs defaults


def test_drift_no_body_uses_model_portfolio():
    """No body → uses the active profile's MODEL portfolio (not DEFAULT_WEIGHTS)."""
    h = _make_handler()
    store.set_active_profile("balanced")
    h._drift(None)
    body = h._sent["body"]
    assert "alerts" in body and "summary" in body
    # balanced model portfolio differs from DEFAULT_WEIGHTS target → drift
    assert len(body["alerts"]) > 0


# ── Portfolio source (decoupled from NS-5) ──────────────────────────────
def test_portfolio_get_default_model():
    h = _make_handler()
    store.set_active_profile("balanced")
    h._portfolio_get()
    d = h._sent["body"]
    assert d["is_model"] is True and d["source"] == "balanced"
    assert set(d["model_portfolios"]) == {"growth", "balanced", "capital_preservation"}
    assert "ns5_portfolios" in d


def test_portfolio_get_ns5(tmp_path, monkeypatch):
    h = _make_handler()
    store.set_active_profile("balanced")
    h._portfolio_get()
    names = h._sent["body"]["ns5_portfolios"]
    if not names:
        return  # nothing to test against
    h._portfolio_set({"source": names[0]})
    assert h._sent["body"]["is_model"] is False
    h._portfolio_get()
    body = h._sent["body"]
    assert body["is_model"] is False and body["source"] == names[0]
    assert body["holdings"]  # non-empty


def test_portfolio_set_invalid_400():
    h = _make_handler()
    h._portfolio_set({"source": "does_not_exist"})
    assert h._sent["status"] == 400
    assert "error" in h._sent["body"]


def test_portfolio_set_model():
    h = _make_handler()
    h._portfolio_set({"source": "model"})
    assert h._sent["body"]["is_model"] is True
    assert h._sent["status"] == 200


def test_ns5_portfolio_weights_normalized_for_drift(tmp_path, monkeypatch):
    """NS-5 holdings are SHARES — must normalize to weights before drift,
    else delta_pct is absurd (e.g. 119900% for a 120-share position)."""
    # Inject an NS-5 store with a v1 flat-shares portfolio.
    import json as _json
    fake = tmp_path / "portfolios.json"
    fake.write_text(_json.dumps({"Tech": {"MSFT": 120, "NVDA": 80}}))
    monkeypatch.setattr(qa_server, "NS5_PORTFOLIOS_PATH", fake)
    h = _make_handler()
    store.set_active_profile("balanced")
    h._portfolio_set({"source": "Tech"})
    h._drift(None)
    body = h._sent["body"]
    assert body["alerts"]
    for a in body["alerts"]:
        # current_wt is a normalized weight in (0,1], delta_pct is sane
        assert 0 <= a["current_wt"] <= 1.0
        assert abs(a["delta_pct"]) < 1000
    # weights path also normalizes (sum to 1)
    h._portfolio_get()
    d = h._sent["body"]
    assert abs(sum(d["holdings"].values()) - 1.0) < 0.01
    assert d["shares"]["MSFT"] == 120.0  # raw shares preserved for modal
