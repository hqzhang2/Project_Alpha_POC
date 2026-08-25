#!/usr/bin/env python3
"""Markup-invariant test — NS-7 v4.8 overrides hydration hook (Ox-owned UI)."""
from pathlib import Path

import pytest

DASH = Path(__file__).resolve().parent.parent / "ns7_dashboard.html"


@pytest.fixture(scope="module")
def html() -> str:
    assert DASH.exists(), f"dashboard missing: {DASH}"
    return DASH.read_text()


def test_loadd1_hydrates_selection_from_overrides(html):
    assert "D1.overrides" in html
    assert "Array.isArray(D1.overrides.keep)" in html
    assert "D1_SELECT = keep ? keep : null;" in html


def test_apply_button_still_present(html):
    assert 'id="d1ApplyBtn"' in html
    assert "applyD1()" in html


def test_overrides_comment_marks_v48_block(html):
    assert "v4.8: hydrate checkbox selection from persisted PM intent" in html
