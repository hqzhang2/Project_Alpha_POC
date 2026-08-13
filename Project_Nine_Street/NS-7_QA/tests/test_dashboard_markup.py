"""Tests for NS-7 dashboard markup invariants (ns7_dashboard.html).

The dashboard is static HTML + JS served fresh per request; its invariants
(Why drill-down, benchmark filter, Major-league list absence) are asserted
here so the PM-facing surface is covered by the canonical suite, not just
ad-hoc verification.

Run: python3 -m pytest NS-7_QA/tests/test_dashboard_markup.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DASH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "ns7_dashboard.html")
_html = open(DASH).read()


def test_top_picks_only_no_major_league_list():
    """PM view is the top-N picks; the Major league list is engine-internal."""
    assert "uniTable" not in _html
    assert "uniSummary" not in _html
    assert "renderUni" not in _html
    assert "Major league — active candidates" not in _html


def test_benchmark_filter_present():
    """Beat-SPY&QQQ filter bar + Vs column."""
    assert 'id="benchFilter"' in _html
    assert "Beat" in _html
    assert "Vs SPY/QQQ" in _html
    assert 'benchCount' in _html
    assert "beats_benchmarks" in _html


def test_why_drilldown_buttons_present():
    """Why buttons on every row + sectioned reason panel."""
    assert 'class="why-btn"' in _html
    assert "Why</button>" in _html
    assert "dsec" in _html
    assert "Quality veto (pick gate)" in _html
    assert "league_reason" in _html
    assert "momentum_window" in _html
    assert "closeDetail" in _html


def test_no_duplicate_let_declarations():
    """Duplicate `let` in the same scope would throw a SyntaxError."""
    assert _html.count("let SEL =") == 1


def test_selection_table_columns():
    """Rank / Ticker / Momentum / Vs SPY&QQQ / Why."""
    assert _html.count("<th") >= 5
    assert "Momentum (6m skip)" in _html
    assert "showDetail" in _html
