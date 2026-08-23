"""Tests for the v4.4 daily badge snapshot (HMM + value screen).

Covers: snapshot loader fail-open (missing/corrupt/stale), passed-framework
extraction, batch builder (per-ticker error isolation, population subset from
selection.json), framework metric formatting, and the dashboard markup
invariants for the new surfaces (pass pills + Fundamental Selection section)
while asserting every pre-existing detail section is untouched.

Run: python3 -m pytest tests/test_vs_badges.py -q   (from NS-7_QA/)
"""
import json
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config   # noqa: E402
import vs_badges  # noqa: E402


def _write_snap(path, generated_at=None, tickers=None):
    snap = {
        "as_of": "2026-08-22",
        "generated_at": generated_at or datetime.now().isoformat(timespec="seconds"),
        "service": "NS-7",
        "population": len(tickers or {}),
        "tickers": tickers or {},
    }
    path.write_text(json.dumps(snap))
    return snap


def _entry(agreement=2, passes=("graham", "buffett"), hmm_signal="FLAT"):
    return {
        "hmm": {"signal": hmm_signal, "color": "#444", "gate": {"gated": False}},
        "value_screen": {
            "agreement": agreement,
            "frameworks": {n: {"pass": n in passes, "metric": f"{n} metric"}
                           for n in ("graham", "greenblatt", "lynch", "buffett")},
        },
    }


# ── loader fail-open ─────────────────────────────────────────────────────

def test_missing_file_returns_none(tmp_path):
    assert vs_badges.load_snapshot(tmp_path / "nope.json") is None
    assert vs_badges.ticker_entry("MU", tmp_path / "nope.json") is None


def test_corrupt_file_returns_none(tmp_path):
    p = tmp_path / "vs.json"
    p.write_text("{not json")
    assert vs_badges.load_snapshot(p) is None


def test_stale_snapshot_returns_none(tmp_path):
    p = tmp_path / "vs.json"
    stale = (datetime.now() - timedelta(hours=config.VS_BADGE_MAX_AGE_HOURS + 2)
             ).isoformat(timespec="seconds")
    _write_snap(p, generated_at=stale, tickers={"MU": _entry()})
    assert vs_badges.load_snapshot(p) is None
    assert vs_badges.ticker_entry("MU", p) is None


def test_fresh_snapshot_ticker_entry(tmp_path):
    p = tmp_path / "vs.json"
    _write_snap(p, tickers={"MU": _entry()})
    e = vs_badges.ticker_entry("mu", p)  # case-insensitive
    assert e is not None
    assert e["hmm"]["signal"] == "FLAT"
    assert e["value_screen"]["agreement"] == 2


def test_absent_ticker_is_none(tmp_path):
    p = tmp_path / "vs.json"
    _write_snap(p, tickers={"MU": _entry()})
    assert vs_badges.ticker_entry("DELL", p) is None


def test_passed_frameworks(tmp_path):
    p = tmp_path / "vs.json"
    _write_snap(p, tickers={"KLAC": _entry(passes=("graham", "lynch", "buffett"))})
    e = vs_badges.ticker_entry("KLAC", p)
    assert vs_badges.passed_frameworks(e) == ["graham", "lynch", "buffett"]


def test_passed_frameworks_none_pass(tmp_path):
    p = tmp_path / "vs.json"
    _write_snap(p, tickers={"DELL": _entry(passes=(), agreement=0)})
    e = vs_badges.ticker_entry("DELL", p)
    assert vs_badges.passed_frameworks(e) == []


def test_hmm_signal_none_on_error(tmp_path):
    p = tmp_path / "vs.json"
    entry = _entry()
    entry["hmm"] = {"signal": None, "error": "yfinance down"}
    _write_snap(p, tickers={"AMD": entry})
    assert vs_badges.hmm_signal(vs_badges.ticker_entry("AMD", p)) is None


# ── batch builder ────────────────────────────────────────────────────────

def test_population_is_benchmark_outperformers_only(tmp_path):
    sel = tmp_path / "selection.json"
    sel.write_text(json.dumps({
        "as_of": "2026-08-22",
        "scores": [
            {"ticker": "DELL", "momentum": 2.9, "outperforms_benchmarks": True},
            {"ticker": "MU", "momentum": 1.6, "outperforms_benchmarks": True},
            {"ticker": "HOOD", "momentum": 0.6, "outperforms_benchmarks": False},
        ]}))
    import vs_badge_refresh as vbr
    tickers, as_of = vbr.load_selection_population(sel)
    assert tickers == ["DELL", "MU"]      # HOOD skipped — not an outperformer
    assert as_of == "2026-08-22"


def test_build_snapshot_isolates_per_ticker_errors(tmp_path):
    sel = tmp_path / "selection.json"
    sel.write_text(json.dumps({
        "as_of": "2026-08-22",
        "scores": [
            {"ticker": "DELL", "momentum": 2.9, "outperforms_benchmarks": True},
            {"ticker": "MU", "momentum": 1.6, "outperforms_benchmarks": True},
        ]}))

    def hmm_ok(t):
        return {"signal": "HOLD LONG", "color": "#22c55e", "gate": {"gated": False}}

    def hmm_boom(t):
        raise RuntimeError("hmm down")

    def screen_ok(t, base=None):
        return {"agreement": 1,
                "frameworks": {n: {"pass": n == "graham", "metric": "m"}
                               for n in ("graham", "greenblatt", "lynch", "buffett")}}

    import vs_badge_refresh as vbr
    snap = vbr.build_snapshot(
        selection_path=sel,
        hmm_fn=lambda t: (hmm_ok(t) if t == "MU" else (_ for _ in ()).throw(RuntimeError("hmm down"))),
        screen_fn=screen_ok)
    assert snap["population"] == 2
    # MU healthy; DELL's HMM error recorded but the value screen still present.
    assert snap["tickers"]["MU"]["hmm"]["signal"] == "HOLD LONG"
    assert "error" in snap["tickers"]["DELL"]["hmm"]
    assert snap["tickers"]["DELL"]["value_screen"]["agreement"] == 1


def test_framework_metric_formatting():
    import vs_badge_refresh as vbr
    assert "P/E" in vbr.framework_metric(
        "graham", {"pe": 109.29, "graham_number": 90.8})
    assert "EY" in vbr.framework_metric("greenblatt", {"ey": 0.0106, "roc": 0.5619})
    assert "PEG" in vbr.framework_metric("lynch", {"peg": 4.17, "growth": 0.2621})
    assert "ROE" in vbr.framework_metric("buffett", {"roe": 0.1576, "fcf_conv": 3.9, "de": 0.0})


# ── dashboard markup invariants ──────────────────────────────────────────

DASH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "ns7_dashboard.html")
_html = open(DASH).read()


def test_dashboard_pass_pills_present():
    assert "vs-badge" in _html
    assert "vsPassPills" in _html
    assert "vsBadgeLoad" in _html
    # Both tables render the pills (top-N + full outperformers).
    assert _html.count("vsPassPills(p.ticker)") == 1
    assert _html.count("vsPassPills(s.ticker)") == 1


def test_dashboard_fundamental_selection_section():
    assert "Fundamental Selection" in _html
    # All four frameworks always shown (PASS + FAIL rows).
    for fw in ("graham", "greenblatt", "lynch", "buffett"):
        assert fw in _html
    assert "PASS" in _html and "FAIL" in _html
    # Snapshot timestamp in the header.
    assert "as of" in _html
    assert "snapGeneratedAt" in _html


def test_dashboard_buffett_row_two_lines():
    """PM rev (Buffett ONLY): line 1 = PASS + FCF conv; line 2 = ROE · D/E.
    Other frameworks stay single-line; header keeps badges + agreement."""
    assert "n === 'buffett'" in _html
    assert "pick('FCF')" in _html and "pick('ROE')" in _html and "pick('D/E')" in _html
    # Header still carries pass badges + agreement + timestamp (not stripped).
    sec = _html[_html.index("dsec\">Fundamental Selection '"):]
    sec = sec[:sec.index("</div>")]
    assert "headBadges" in sec and "vsc.agreement" in sec


def test_dashboard_existing_sections_untouched():
    """The ONLY new detail section is Fundamental Selection — everything else
    keeps its exact markup, and the render order is sels → lg → ns2 → vs → qv → fx."""
    # The render concatenation must keep the new `vs` section between the
    # NS-2 advisory block and Quality veto; nothing else reordered.
    assert "sels + lg + ns2 + vs + qv + fx" in _html
    # Pre-existing section markup unchanged.
    assert '<div class="dsec">Selection</div>' in _html
    assert "'<div class=\"dsec\">League — '" in _html
    assert '<div class="dsec">NS-2 HMM (advisory)</div>' in _html
    assert 'Not on the NS-2 watchlist — neutral.' in _html
    assert '<div class="dsec">Quality veto (pick gate)</div>' in _html
    assert '<div class="dsec">Facts</div>' in _html


def test_dashboard_fail_open_no_legend():
    """Fail-open: fetch failure empties the map; no legend text on the page."""
    assert "VSBADGES = {}" in _html
    assert ".catch(() => { VSBADGES = {};" in _html
    assert "Pass badges:" not in _html


def test_dashboard_hmm_pill_all_signals():
    """/api/vsbadges HMM signals (incl. NO-EDGE) render on every table row,
    colored by the canonical signal color from the snapshot."""
    assert "vsHmmPill" in _html
    assert "vs-hmm" in _html
    # Pill precedes the value-screen pass pills (HMM first).
    assert _html.index("return vsHmmPill(ticker) + passHtml") > 0
    assert "h.color" in _html          # canonical SIGNAL_COLORS from snapshot
    assert "h.signal" in _html         # every signal incl. NO-EDGE — no filtering
    assert "WF " in _html              # walk-forward gate verdict in tooltip


def test_dashboard_legacy_flag_suppressed_when_batch_has_hmm():
    """No double HMM badge: the amber legacy flag renders only when the daily
    batch snapshot has no HMM signal for the name (snapshot = source of truth)."""
    assert "!(VSBADGES[p.ticker] && VSBADGES[p.ticker].hmm && VSBADGES[p.ticker].hmm.signal)" in _html


# ── server route ─────────────────────────────────────────────────────────

def test_server_has_vsbadges_route():
    srv = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "qa_server.py")
    src = open(srv).read()
    assert '"/api/vsbadges"' in src
    assert "_vsbadges" in src
    assert "vs_badges.load_snapshot()" in src
