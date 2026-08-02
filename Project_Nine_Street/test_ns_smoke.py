#!/usr/bin/env python3
"""
Track 0 — Nine Street QA server integration smoke tests.

Purpose: catch the "blank tab / silent 500" failure mode at the route layer
without binding a socket (the live servers already hold the real ports).

For each NS server we import its request handler, drive `do_GET` with a fake
request object, and assert:
  * `/health` returns 200 with a valid JSON body (server is up / wired),
  * the server's primary data route returns *some* valid HTTP status without
    raising (a crash here = the blank-tab regression).

Per-server primary route:
  NS-1 -> /api/chart | NS-2 -> /api/ticker | NS-3 -> /api/v1/tier1 | NS-4 -> /api/v1/all

Run:  pytest test_ns_smoke.py
"""
import os
import sys
import json
import types
import importlib

PROJECT = os.path.dirname(os.path.abspath(__file__))


class FakeRequest:
    """Minimal stand-in for a BaseHTTPRequestHandler request."""
    def __init__(self, path):
        self.path = path
        self.status = None
        self.headers = {}
        self.body = b""

    def send_response(self, code):
        self.status = code

    def send_header(self, k, v):
        self.headers[k] = v

    def end_headers(self):
        pass

    def write(self, data):
        if isinstance(data, str):
            data = data.encode()
        self.body += data

    def _json(self, code, payload):
        self.send_response(code)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.write(json.dumps(payload))


def _load_handler(module_file):
    """Import a server module and return its do_GET handler class + module."""
    mod_name = os.path.splitext(os.path.basename(module_file))[0]
    spec = importlib.util.spec_from_file_location(mod_name, module_file)
    mod = importlib.util.module_from_spec(spec)
    # run the module in its own dir so relative imports / file opens resolve
    cwd = os.path.dirname(module_file)
    old = os.getcwd()
    try:
        os.chdir(cwd)
        sys.path.insert(0, cwd)
        spec.loader.exec_module(mod)
    finally:
        os.chdir(old)
    # Find the Handler class defined in this module
    handler = None
    for attr in vars(mod).values():
        if isinstance(attr, type) and hasattr(attr, "do_GET"):
            # pick the one whose module matches (avoid imported bases)
            if getattr(attr, "__module__", None) == mod_name:
                handler = attr
                break
    if handler is None:
        raise RuntimeError(f"No do_GET handler found in {module_file}")
    return mod, handler


def _drive(handler_cls, path):
    req = FakeRequest(path)
    # The handler's __init__ normally takes a socket; we skip it and set attrs
    inst = handler_cls.__new__(handler_cls)
    # bind the fake methods
    inst.path = req.path
    inst.send_response = req.send_response
    inst.send_header = req.send_header
    inst.end_headers = req.end_headers
    inst.wfile = types.SimpleNamespace(write=req.write)
    inst._json = req._json
    try:
        inst.do_GET()
    except Exception as e:  # a crash here is the regression we're testing for
        return req, e
    return req, None


# (module file, primary data route)
SERVERS = [
    ("NS_1_QA/server_qa.py", "/api/chart"),
    ("NS-2_QA/qa_server.py", "/api/ticker"),
    ("NS-3_QA/qa_server.py", "/api/v1/tier1"),
    ("NS-4_QA/qa_server.py", "/api/v1/all"),
]

# PROD servers. Each must exist and expose a do_GET handler (stdlib pattern).
# P7-A completed: NS-3_PROD / NS-4_PROD ported from FastAPI to stdlib (see
# remediation plan) - no longer skipped.
PROD_SERVERS = [
    ("NS-1_PROD/server_qa.py", "/health"),
    ("NS-2_PROD/qa_server.py", "/health"),
    ("NS-3_PROD/backend/main.py", "/api/v1/health"),
    ("NS-4_PROD/backend/main.py", "/api/v1/health"),
]

# Alpha Terminal PROD (stdlib server.py pattern)
ALPHA_PROD_SERVER = "Project_Sequoia/terminal/server.py"


def test_alpha_prod_server_imports_and_health():
    f = os.path.join(PROJECT, ALPHA_PROD_SERVER)
    if not os.path.exists(f):
        return  # not checked out on this branch
    mod, handler = _load_handler(f)
    assert hasattr(handler, "do_GET")
    req, err = _drive(handler, "/health")
    assert err is None, f"Alpha PROD /health raised: {err}"
    assert req.status == 200, f"Alpha PROD /health status={req.status}"
    body = json.loads(req.body.decode())
    assert body.get("service") == "alpha-terminal", f"wrong service: {body}"


def test_all_prod_servers_import_and_expose_handler():
    for entry in PROD_SERVERS:
        rel, route = entry[0], entry[1]
        f = os.path.join(PROJECT, rel)
        if not os.path.exists(f):
            continue  # PROD dir not checked out on this branch yet
        mod, handler = _load_handler(f)
        assert hasattr(handler, "do_GET")
        req, err = _drive(handler, route)
        assert err is None, f"{rel} {route} raised: {err}"
        # health routes MUST return 200 - a 404 here means the route is
        # missing (caught exactly this bug on NS-4_PROD in P7-A)
        assert req.status == 200, f"{rel} {route} status={req.status}"


def test_all_ns_servers_import_and_expose_handler():
    for rel, _ in SERVERS:
        f = os.path.join(PROJECT, rel)
        if not os.path.exists(f):
            continue
        mod, handler = _load_handler(f)
        assert hasattr(handler, "do_GET")
        # /health must be wired and return 200 with valid JSON
        req, err = _drive(handler, "/health")
        assert err is None, f"{rel} /health raised: {err}"
        assert req.status == 200, f"{rel} /health status={req.status}"
        body = json.loads(req.body.decode())
        assert "status" in body, f"{rel} /health body missing 'status': {body}"


def test_all_ns_primary_route_responds_without_crash():
    for rel, route in SERVERS:
        f = os.path.join(PROJECT, rel)
        if not os.path.exists(f):
            continue
        mod, handler = _load_handler(f)
        req, err = _drive(handler, route)
        # A crash (unhandled exception) is the blank-tab regression -> fail.
        assert err is None, f"{rel} {route} raised: {err}"
        # Must return a real HTTP status (not hang / not None)
        assert req.status is not None, f"{rel} {route} returned no status"
        # Acceptable: 200 (data) or 4xx/503 (engines/data unavailable, graceful)
        assert req.status in (200, 400, 404, 503), (
            f"{rel} {route} unexpected status {req.status}"
        )
