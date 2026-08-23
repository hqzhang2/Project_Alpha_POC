#!/usr/bin/env python3
"""
NS-5 v4.5 — Ox mechanical-slice removal tests.

Verifies that the Hyperscaler static portfolio, portfolio-edit (New/Save/Delete),
and policy CRUD were removed from the server + dashboard, and that the new
feed-source selector (D1/NS8/NSETF/ALL via GET /api/sources + /api/grade
{source: ...}) is present and wired.

Run (clean env, no network):
  env -i HOME=$HOME /usr/bin/python3 -m pytest tests/test_v45_removal.py -q

These are markup/route invariants — they do NOT depend on the sklearn/numpy
ABI issues that break the frontier/regime/drift suites under the hermes venv.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DASH_PATH = ROOT / "ns5_dashboard.html"


# ---------------------------------------------------------------------------
# Dashboard markup invariants
# ---------------------------------------------------------------------------

def _dash():
    return DASH_PATH.read_text()


class TestDashboardMarkup:
    def test_removed_portfolio_editor(self):
        html = _dash()
        assert 'id="portfolioSelect"' not in html
        assert 'id="holdingsBody"' not in html
        assert 'id="portfolioName"' not in html
        assert 'newPortfolio' not in html
        assert 'savePortfolio' not in html
        assert 'deletePortfolio' not in html

    def test_removed_policy_panel(self):
        html = _dash()
        assert 'id="policySelect"' not in html
        assert 'id="policyDetail"' not in html
        assert 'showPolicy' not in html

    def test_removed_hyperscaler_helpers(self):
        html = _dash()
        for fn in ("loadPortfolio", "onPortfolioSelect", "collectRows",
                   "selectedPortfolioName", "selectedPolicyName",
                   "renderRows", "renderLots", "addLot"):
            assert fn not in html

    def test_source_dropdown_present(self):
        html = _dash()
        assert 'id="sourceSelect"' in html
        assert 'onSourceSelect' in html

    def test_source_options_seed_present(self):
        html = _dash()
        # Static fallback seed should exist so the dropdown works even before
        # /api/sources returns (fail-open).
        assert "D1" in html and "NS8" in html
        assert "NSETF" in html and "ALL" in html

    def test_grade_posts_source(self):
        html = _dash()
        assert "selectedSource()" in html
        assert "{source: source}" in html

    def test_frontier_uses_source(self):
        html = _dash()
        assert "JSON.stringify({source: source})" in html

    def test_grade_button_renamed(self):
        html = _dash()
        assert "Grade Source" in html


# ---------------------------------------------------------------------------
# Server route invariants
# ---------------------------------------------------------------------------

SERVER_SRC = (ROOT / "qa_server.py").read_text()


class TestServerRoutes:
    def test_get_portfolios_removed(self):
        # The GET dispatcher must no longer route /api/portfolios.
        get_section = SERVER_SRC.split("def do_GET")[1].split("def do_POST")[0]
        assert "/api/portfolios" not in get_section
        assert "/api/policies" not in get_section

    def test_post_portfolios_removed(self):
        post_section = SERVER_SRC.split("def do_POST")[1].split("def do_DELETE")[0]
        assert "/api/portfolios" not in post_section
        assert "/api/policies" not in post_section

    def test_delete_crud_removed(self):
        # do_DELETE must no longer reference portfolio/policy delete helpers.
        delete_section = SERVER_SRC.split("def do_DELETE")[1].split("def log_message")[0]
        assert "portfolio" not in delete_section.lower()
        assert "policy" not in delete_section.lower()

    def test_crud_helpers_removed(self):
        for fn in ("_portfolios_get", "_policies_get", "_portfolios_post",
                   "_policies_post", "_portfolios_delete", "_policies_delete"):
            assert fn not in SERVER_SRC

    def test_resolve_for_drift_kept(self):
        # Drift + regime axes still resolve holdings/policy names — must stay.
        assert "_resolve_for_drift" in SERVER_SRC

    def test_sources_route_present(self):
        assert "/api/sources" in SERVER_SRC
        assert "source_availability" in SERVER_SRC

    def test_grade_source_resolution(self):
        assert "feed_sources.load_source(source)" in SERVER_SRC


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
