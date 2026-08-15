"""tests/test_server.py — NS-8 Server Verification (stdlib http.server).

Canonical tests for qa_server.py (now stdlib http.server, not FastAPI).
Spins up the real HTTPServer on a scratch port and exercises the endpoints,
matching the other NS services' verification approach.
"""
import json
import os
import sys
import threading
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import qa_server  # noqa: E402

SCRATCH_PORT = 9381


def _start_server():
    os.environ["PORT"] = str(SCRATCH_PORT)
    os.environ["ENV"] = "QA"
    qa_server.store.init_db()
    qa_server.store.init_tranche_state()
    from http.server import HTTPServer
    srv = HTTPServer(("0.0.0.0", SCRATCH_PORT), qa_server.NS8Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


def _get(path, headers=None):
    req = urllib.request.Request(f"http://localhost:{SCRATCH_PORT}{path}",
                                 headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, dict(resp.headers), resp.read()


_server = None


def setup_module():
    global _server
    _server = _start_server()


def teardown_module():
    if _server:
        _server.shutdown()


def test_health_ok():
    """Health endpoint returns service metadata + env."""
    status, _, body = _get("/health")
    assert status == 200
    data = json.loads(body)
    assert data["service"] == "NS-8"
    assert data["env"] == "QA"
    assert data["config"]["sma_window"] == 200
    assert data["config"]["tranches"] == 4


def test_health_cors_header():
    """Cross-origin health poll (from portal :8000) must expose CORS."""
    status, headers, _ = _get("/health", headers={"Origin": "http://localhost:8000"})
    assert status == 200
    assert headers.get("Access-Control-Allow-Origin") == "*"


def test_preflight_cors():
    """OPTIONS preflight carries CORS allow headers."""
    req = urllib.request.Request(
        f"http://localhost:{SCRATCH_PORT}/health",
        headers={
            "Origin": "http://localhost:8000",
            "Access-Control-Request-Method": "GET",
        }, method="OPTIONS")
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.status in (200, 204)
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"


def test_dashboard_served():
    """Dashboard HTML is served at /dashboard with CORS."""
    status, headers, body = _get("/dashboard")
    assert status == 200
    assert b"NS-8 Tactical Allocation" in body
    assert headers.get("Access-Control-Allow-Origin") == "*"


def test_root_serves_dashboard():
    """Root / also serves the dashboard."""
    status, _, body = _get("/")
    assert status == 200
    assert b"NS-8" in body


def test_tranche_structure():
    """Tranche endpoint returns current + total + schedule."""
    status, _, body = _get("/api/tranche")
    assert status == 200
    data = json.loads(body)
    assert data["total"] == 4
    assert "current" in data
    assert "schedule" in data


def test_unknown_route_404():
    """Unknown route returns 404."""
    try:
        _get("/api/nonexistent")
        assert False, "expected 404"
    except urllib.error.HTTPError as e:
        assert e.code == 404


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])