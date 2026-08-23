#!/usr/bin/env python3
"""v4.5 markup + route invariants — NS-5 grades feed sources, not stored books.

Dashboard invariants:
  - sourceSelect dropdown EXISTS with D1/NS8/NSETF/ALL wiring (onSourceSelect,
    selectedSource, /api/sources fetch)
  - portfolio/policy edit surface GONE (no portfolioSelect/policySelect CRUD)
Server-route invariants (source-text level, no live server):
  - portfolio/policy CRUD helpers and seed_if_missing are gone from qa_server.py
  - grade + frontier accept {source} via feed_sources.load_source

Run: python3 -m pytest tests/test_v45_feed_surface.py -q   (from NS-5_QA/)
"""
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
DASH = (BASE / "ns5_dashboard.html").read_text()
SERVER = (BASE / "qa_server.py").read_text()


# ── Dashboard: new source surface present ────────────────────────────────
def test_source_select_exists():
    assert 'id="sourceSelect"' in DASH
    assert "onSourceSelect()" in DASH


def test_source_dropdown_populated_from_api_sources():
    assert "/api/sources" in DASH
    assert "selectedSource" in DASH


def test_grade_posts_source():
    assert "{source: source}" in DASH or "body = {source: source}" in DASH


# ── Dashboard: portfolio/policy edit surface removed ────────────────────
@pytest.mark.parametrize("gone", [
    "portfolioSelect", "policySelect", "newPortfolio", "savePortfolio",
    "deletePortfolio", "onPortfolioSelect", "onPolicySelect",
    "collectRows", "Delete Portfolio",
    # v4.5 Annex 2: Sleeve Blend panel + loadBlend migrated to NS-7 D1 basket
    "Sleeve Blend", "blendBox", "loadBlend", "/api/blend",
])
def test_removed_ui_elements_absent(gone):
    assert gone not in DASH


# ── Server: CRUD routes/helpers retired ──────────────────────────────────
@pytest.mark.parametrize("gone", [
    "_portfolios_get", "_policies_get", "_portfolios_post", "_policies_post",
    "_portfolios_delete", "_policies_delete", "seed_if_missing",
])
def test_removed_server_helpers_absent(gone):
    assert gone not in SERVER


def test_sources_route_present():
    assert "/api/sources" in SERVER
    assert "feed_sources.source_availability" in SERVER


def test_grade_and_frontier_resolve_source():
    assert "feed_sources.load_source" in SERVER
    # both grade and frontier paths consult the source param
    assert 'body.get("source")' in SERVER


def test_config_declares_feed_sources():
    import config
    assert config.FEED_SOURCES == ("D1", "NS8", "NSETF", "ALL")
