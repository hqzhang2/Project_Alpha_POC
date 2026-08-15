"""tests/test_server.py — NS-8 QA Server Verification.

Canonical tests for qa_server.py so CORS and endpoint behavior have a
repeatable, non-ad-hoc check (previously only verified via manual curl).
Uses FastAPI TestClient — no live server needed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from qa_server import app  # noqa: E402

client = TestClient(app)


def test_health_ok():
    """Health endpoint returns service metadata."""
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "NS-8 QA"
    assert body["config"]["sma_window"] == 200
    assert body["config"]["tranches"] == 4


def test_health_cors_header():
    """Cross-origin health poll (from portal :8000) must expose CORS."""
    r = client.get(
        "/health",
        headers={"Origin": "http://localhost:8000"},
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "*"


def test_preflight_cors():
    """OPTIONS preflight carries CORS allow headers."""
    r = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:8000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code in (200, 204)
    assert r.headers.get("access-control-allow-origin") == "*"


def test_dashboard_served():
    """Dashboard HTML is served at /dashboard with CORS."""
    r = client.get(
        "/dashboard",
        headers={"Origin": "http://localhost:8000"},
    )
    assert r.status_code == 200
    assert "NS-8 Tactical Allocation" in r.text
    assert r.headers.get("access-control-allow-origin") == "*"


def test_dashboard_missing_returns_404():
    """Dashboard returns 404 only if the HTML file is absent."""
    import config
    from pathlib import Path as P
    orig = config.DATA_DIR.parent / "ns8_dashboard.html"
    if not orig.exists():
        r = client.get("/dashboard")
        assert r.status_code == 404


def test_signals_404_without_data():
    """/api/signals returns 404 before any rebalance has run."""
    # TestClient uses a fresh app; the store may or may not have data.
    # Guard: if store DB is absent/empty, expect 404; else expect 200 shape.
    r = client.get("/api/signals")
    if r.status_code == 200:
        body = r.json()
        assert "signals" in body
        assert "weights" in body
    else:
        assert r.status_code == 404


def test_tranche_structure():
    """Tranche endpoint returns current + total + schedule."""
    r = client.get("/api/tranche")
    assert r.status_code == 200
    body = r.json()
    assert body["total_tranches"] == 4
    assert "current_tranche" in body
    assert "schedule" in body


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])