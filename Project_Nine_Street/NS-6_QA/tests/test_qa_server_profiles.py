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
