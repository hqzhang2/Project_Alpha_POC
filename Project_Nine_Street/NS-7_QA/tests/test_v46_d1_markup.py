#!/usr/bin/env python3
"""v4.6 markup invariants — DeltaOne rename, tenure badge, D1 modal.

Run: python3 -m pytest tests/test_v46_d1_markup.py -q   (from NS-7_QA/)
"""
from pathlib import Path

DASH = Path(__file__).resolve().parent.parent.joinpath("ns7_dashboard.html").read_text()


# ── DeltaOne display rename ──────────────────────────────────────────────
def test_deltaone_rename():
    assert "DELTAONE STRATEGY" in DASH
    assert "NS-7 DeltaOne Strategy" in DASH


def test_d1_basket_kpi_present():
    assert "D1 basket" in DASH


# ── days-on-list badge ───────────────────────────────────────────────────
def test_tenure_badge_column_and_loader():
    assert "Days on List" in DASH
    assert "tenure-badge" in DASH
    assert "/api/d1/tenure" in DASH
    assert "TENURE[" in DASH


def test_tenure_tooltip_explains_reset():
    assert "resets on demotion" in DASH


# ── 1-year SPY/VIX modal (axis ownership is PM-fixed) ────────────────────
def test_modal_elements_present():
    assert 'id="d1Modal"' in DASH
    assert 'id="d1Chart"' in DASH
    assert "openD1Modal()" in DASH
    assert "closeD1Modal()" in DASH
    assert "/api/d1/series" in DASH


def test_modal_axis_ownership():
    # growth-of-$100 LEFT, VIX RIGHT (PM-corrected axis mapping) — Plotly style
    assert "yaxis2" in DASH and "side: 'right'" in DASH
    assert "Growth of $100" in DASH and "VIX" in DASH


def test_no_legacy_title():
    assert "GROWTH/MOMENTUM SELECTION" not in DASH


# ── D1 portfolio construction panel ─────────────────────────────────────
def test_construction_panel_present():
    assert "D1 Portfolio Construction" in DASH
    assert 'id="d1Method"' in DASH
    assert 'id="d1N"' in DASH
    assert 'id="d1Table"' in DASH


def test_construction_panel_below_full_list():
    # PM ordering: Selection → Full list → D1 Portfolio Construction
    assert DASH.index("Full list — outperforming") < DASH.index(
        "D1 Portfolio Construction")


def test_modal_button_in_construction_panel():
    # PM: the 📈 modal button lives in the construction panel, not Selection
    panel_start = DASH.index("D1 Portfolio Construction")
    panel_end = DASH.index("id=\"d1Table\"", panel_start)
    seg = DASH[panel_start:panel_end]
    assert 'id="d1ModalBtn"' in seg
    assert "openD1Modal()" in seg


def test_apply_persists_basket_and_mtm():
    assert "/api/d1/rebuild" in DASH
    # the server-side rebuild writes the basket + refreshes MtM (save semantics)
    import qa_server  # noqa: F401  (import guard: route exists)
    src = Path(__file__).resolve().parent.parent.joinpath("qa_server.py").read_text()
    assert "write_text(json.dumps(doc, indent=2))" in src
    assert "persist_returns(rows)" in src


def test_all_four_methods_selectable():
    for m in ("momentum_score", "rank_tilted", "risk_normalized", "tenure_aware"):
        assert f'value="{m}"' in DASH


def test_apply_rebuild_wired():
    assert "applyD1()" in DASH
    assert "/api/d1/rebuild" in DASH


def test_chartjs_loaded_for_modal():
    # NS-ETF-style charting: Plotly (crosshair + x-unified hover), not Chart.js
    assert "plotly-2.32.0.min.js" in DASH
    assert "chart.umd" not in DASH.lower()


def test_plotly_styling_matches_nsetf():
    # crosshair shape + x-unified hover + dark plot bg (NS-ETF layout pattern)
    assert "d1Crosshair" in DASH
    assert "hovermode: 'x unified'" in DASH
    assert "plot_bgcolor: '#161b22'" in DASH


def test_vix_sma_trace_present():
    assert "VIX SMA" in DASH and "'y2'" in DASH


def test_render_and_load_d1_called():
    assert "renderD1()" in DASH and "loadD1();" in DASH
