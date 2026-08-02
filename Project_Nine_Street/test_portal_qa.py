#!/usr/bin/env python3
"""
Unit tests for Project_Nine_Street/portal.py

Covers the two logic units that have regressed in production:
  * build_html()  - HTML/CSS/JSON assembly (broke twice: .format() KeyError,
                    unescaped {{ }}, and JSON brace corruption)
  * PortalHandler - HTTP GET routes (/, /api/health, 404) + CORS header

Plus an invariant test for the QA=PROD+1 port scheme.

Run:  python3 -m pytest Project_Nine_Street/test_portal_qa.py -v
"""
import importlib.util
import json
import os
import re
import threading
import urllib.error
import urllib.request

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SPEC = importlib.util.spec_from_file_location(
    "portal_ut", os.path.join(_HERE, "portal.py")
)
portal = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(portal)


# --------------------------------------------------------------------------- #
# build_html()
# --------------------------------------------------------------------------- #
def test_build_html_returns_str():
    html = portal.build_html()
    assert isinstance(html, str)
    assert len(html) > 1000  # a real page, not an error/empty body
    assert html.strip().endswith("</html>")


def test_build_html_no_double_braces():
    """Regression guard: HTML_TEMPLATE uses {{ }} escaping for the old
    .format() era. If build_html forgets to unescape them, every CSS rule
    becomes invalid and the browser ignores all styling (white bg, tiny
    frame).

    The only legitimate '}}' in the output is the trailing close of the
    STRATS JSON (json.dumps), so we flag it only when it leaks into the
    CSS/JS braces — i.e. paired '{{'/'}}' outside the STRATS token.
    """
    html = portal.build_html()
    assert "{{" not in html, "leftover '{{' -> CSS/JS braces not unescaped"
    # Strip the STRATS JSON token, then check no stray '}}' remains in CSS/JS.
    stripped = re.sub(r"const STRATS = \{.*?\};", "", html, flags=re.S)
    assert "}}" not in stripped, "leftover '}}' in CSS/JS -> broken styles"


def test_build_html_strats_json_valid():
    """Regression guard: a prior fix corrupted STRATS JSON by stripping a
    closing brace, which made the inline <script> throw and left the portal
    stuck on 'Loading...'. The JSON must parse and carry the right ports."""
    html = portal.build_html()
    m = re.search(r"const STRATS = (\{.*?\});", html, re.S)
    assert m, "STRATS token missing from output"
    data = json.loads(m.group(1))  # must not raise
    assert set(data) == {"alpha", "ns1", "ns2", "ns3", "ns4"}
    assert data["alpha"]["qa"] == 9099
    assert data["ns1"]["qa"] == 9219
    assert data["ns2"]["qa"] == 9229
    assert data["ns2"]["path"] == ""
    assert data["ns3"]["qa"] == 9237
    assert data["ns4"]["qa"] == 9241


def test_build_html_placeholders_consumed():
    """{ts} and {strategies_json} are literal tokens, not template engines.
    If they survive into the output the page is malformed."""
    html = portal.build_html()
    assert "{ts}" not in html
    assert "{strategies_json}" not in html


def test_build_html_dark_theme_and_full_height():
    """Regression guard for the 'tiny frame / wrong background' bug: the dark
    theme and the height:100% chains must be present and unescaped."""
    html = portal.build_html()
    assert "background: #0a0a0f;" in html          # dark page bg
    assert "html, body { height: 100%; }" in html    # viewport fill
    assert ".main-frame iframe {" in html
    assert "height: 100%" in html                    # frame fills viewport


def test_build_html_alpha_autoload_present():
    """The initial Alpha Terminal load line must be wired into the served HTML."""
    html = portal.build_html()
    assert "localhost:9099/dashboard.html" in html


def test_health_indicator_lives_on_dot_not_tab_border():
    """Health status must be shown by the status dot (the small orange circle),
    not by a colored left border on the tab (the red annotation we removed).

    Contract:
      * CSS must NOT style a health border on .nav-tab (tab-border removed)
      * CSS must style .status-indicator.up / .status-indicator.down
      * JS must add the 'up'/'down' class to the dot (.status-indicator),
        not to the tab.
    """
    html = portal.build_html()
    # 1) tab-border health styling is gone
    assert ".nav-tab.status-up" not in html
    assert ".nav-tab.status-down" not in html
    # 2) dot health styling exists
    assert ".status-indicator.up" in html
    assert ".status-indicator.down" in html
    # 3) JS retargets the dot (querySelector for the dot) and toggles up/down
    assert "querySelector('.status-indicator')" in html
    assert "dot.classList.add(isUp ? 'up' : 'down')" in html
    # 4) the JS must NOT add the health class to the tab itself
    assert "tab.classList.add(isUp ? 'up' : 'down')" not in html




# --------------------------------------------------------------------------- #
def test_strategies_qa_is_prod_plus_one():
    """QA port scheme is PROD+1 (ops convention). Lock it in."""
    for key, cfg in portal.STRATEGIES.items():
        assert cfg["qa"] == cfg["prod"] + 1, f"{key}: qa must equal prod+1"


def test_strategies_all_keys_present():
    assert set(portal.STRATEGIES) == {"alpha", "ns1", "ns2", "ns3", "ns4"}
    for key, cfg in portal.STRATEGIES.items():
        assert "name" in cfg and "path" in cfg
        assert cfg["prod"] and cfg["qa"]


# --------------------------------------------------------------------------- #
# PortalHandler (live HTTP via ephemeral port — does not touch running :8000)
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def server():
    srv = portal.HTTPServer(("127.0.0.1", 0), portal.PortalHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


def _get(server, path):
    port = server.server_address[1]
    return urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5)


def test_root_returns_html_with_cors(server):
    with _get(server, "/") as resp:
        assert resp.status == 200
        assert resp.headers.get("Content-type") == "text/html"
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"
        body = resp.read().decode()
    assert "Trading Strategy Engine" in body
    assert "const STRATS" in body


def test_root_index_html_alias(server):
    with _get(server, "/index.html") as resp:
        assert resp.status == 200


def test_api_health_returns_statuses(server):
    with _get(server, "/api/health") as resp:
        assert resp.status == 200
        assert "application/json" in resp.headers.get("Content-type", "")
        data = json.loads(resp.read())
    assert set(data) == {"alpha", "ns1", "ns2", "ns3", "ns4"}
    assert all(v in ("up", "down") for v in data.values())


def test_unknown_path_returns_404(server):
    port = server.server_address[1]
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/does-not-exist")
    assert exc.value.code == 404


def test_health_loop_polls_all_tabs_including_ns2():
    """Regression: updateStatusIndicators must poll ns2 too, or its dot stays dark."""
    html = portal.build_html()
    # the JS loop that drives the status dots must include ns2
    m = re.search(r"const strategies = \[([^\]]*)\]", html)
    assert m, "strategies loop not found in html"
    assert "'ns2'" in m.group(1), "ns2 missing from health-poll loop -> dark dot"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
