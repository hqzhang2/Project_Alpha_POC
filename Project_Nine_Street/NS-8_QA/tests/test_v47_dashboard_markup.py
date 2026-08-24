#!/usr/bin/env python3
"""Markup-invariant test — NS-8 v4.7 dashboard Book Construction panel.

Ox-owned UI test: verifies the served dashboard markup contains the v4.7
construction card and its render hook, independent of styling.
"""
from pathlib import Path

import pytest

DASH = Path(__file__).resolve().parent.parent / "ns8_dashboard.html"


@pytest.fixture(scope="module")
def html() -> str:
    assert DASH.exists(), f"dashboard missing: {DASH}"
    return DASH.read_text()


def test_has_book_construction_card(html):
    assert "Book Construction" in html


def test_has_construction_container_and_renderer(html):
    assert 'id="construction-content"' in html
    assert "function renderConstruction(" in html
    assert "renderConstruction(doc);" in html


def test_construction_surfaces_v47_fields(html):
    for token in ("gross_risk_exposure", "eff_n", "max_weight",
                  "method", "signal_method"):
        assert token in html


def test_loadsignals_wires_construction(html):
    # renderConstruction must be invoked from loadSignals (same fetch as weights)
    load_block = html.split("async function loadSignals()", 1)[1]
    assert "renderConstruction(doc);" in load_block
