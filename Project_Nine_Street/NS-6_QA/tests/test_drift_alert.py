"""Test drift_alert.py — drift check, NS-2 regime gating, urgency, summary."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import drift_alert
from config import load_theta


def _check(cur, tgt, n2=None):
    return drift_alert.run_drift_check(cur, tgt, n2)


# ── band threshold ───────────────────────────────────────────────────────
def test_below_band_no_alert():
    # AAPL 5.5% vs 5% target → rel 10% < 20% band → no alert
    cur = {"AAPL": 0.055, "BIL": 0.945}
    tgt = {"AAPL": 0.05, "BIL": 0.95}
    r = _check(cur, tgt)
    assert r["alerts"] == []


def test_above_band_triggers_alert():
    # AAPL 25% overweight (0.0625 vs 0.05 = 25% > 20% band) → alert
    cur = {"AAPL": 0.0625, "BIL": 0.9375}
    tgt = {"AAPL": 0.05, "BIL": 0.95}
    r = _check(cur, tgt)
    assert len(r["alerts"]) == 1
    assert r["alerts"][0]["ticker"] == "AAPL"


def test_overweight_alert():
    cur = {"AAPL": 0.08, "BIL": 0.92}
    tgt = {"AAPL": 0.05, "BIL": 0.95}
    r = _check(cur, tgt)
    assert r["alerts"][0]["ticker"] == "AAPL"
    assert r["alerts"][0]["direction"] == "overweight"


def test_underweight_alert():
    cur = {"AAPL": 0.03, "BIL": 0.97}
    tgt = {"AAPL": 0.05, "BIL": 0.95}
    r = _check(cur, tgt)
    assert r["alerts"][0]["direction"] == "underweight"


# ── removal candidate (target == 0) ─────────────────────────────────────
def test_removal_candidate_always_flagged():
    cur = {"MSFT": 0.10, "BIL": 0.90}
    tgt = {"BIL": 0.90}  # MSFT dropped from universe
    r = _check(cur, tgt)
    a = [x for x in r["alerts"] if x["ticker"] == "MSFT"][0]
    assert a["is_removal"] is True
    assert a["delta_pct"] == 100.0


# ── NS-2 regime gating matrix ────────────────────────────────────────────
def test_overweight_trending_monitor():
    cur = {"AAPL": 0.08, "BIL": 0.92}
    tgt = {"AAPL": 0.05, "BIL": 0.95}
    n2 = {"AAPL": {"regime": "TRENDING"}}
    r = _check(cur, tgt, n2)
    a = [x for x in r["alerts"] if x["ticker"] == "AAPL"][0]
    assert "MONITOR" in a["recommendation"]


def test_overweight_meanrev_consider():
    cur = {"AAPL": 0.08, "BIL": 0.92}
    tgt = {"AAPL": 0.05, "BIL": 0.95}
    n2 = {"AAPL": {"regime": "MEAN_REV"}}
    r = _check(cur, tgt, n2)
    a = [x for x in r["alerts"] if x["ticker"] == "AAPL"][0]
    assert "Consider trim" in a["recommendation"]


def test_overweight_crisis_reduce():
    cur = {"AAPL": 0.08, "BIL": 0.92}
    tgt = {"AAPL": 0.05, "BIL": 0.95}
    n2 = {"AAPL": {"regime": "CRISIS"}}
    r = _check(cur, tgt, n2)
    a = [x for x in r["alerts"] if x["ticker"] == "AAPL"][0]
    assert "Reduce" in a["recommendation"]


def test_underweight_trending_opportunity():
    cur = {"AAPL": 0.03, "BIL": 0.97}
    tgt = {"AAPL": 0.05, "BIL": 0.95}
    n2 = {"AAPL": {"regime": "TRENDING"}}
    r = _check(cur, tgt, n2)
    a = [x for x in r["alerts"] if x["ticker"] == "AAPL"][0]
    assert "Opportunity" in a["recommendation"]


def test_ns2_absent_defaults_meanrev():
    cur = {"AAPL": 0.08, "BIL": 0.92}
    tgt = {"AAPL": 0.05, "BIL": 0.95}
    r = _check(cur, tgt, {})  # no ns2 → MEAN_REV
    a = [x for x in r["alerts"] if x["ticker"] == "AAPL"][0]
    assert a["ns2_regime"] == "MEAN_REV"


def test_ns2_unknown_normalises_meanrev():
    cur = {"AAPL": 0.08, "BIL": 0.92}
    tgt = {"AAPL": 0.05, "BIL": 0.95}
    n2 = {"AAPL": {"regime": "SOMETHING_WEIRD"}}
    r = _check(cur, tgt, n2)
    a = [x for x in r["alerts"] if x["ticker"] == "AAPL"][0]
    assert a["ns2_regime"] == "MEAN_REV"


def test_ns2_tuple_input():
    cur = {"AAPL": 0.08, "BIL": 0.92}
    tgt = {"AAPL": 0.05, "BIL": 0.95}
    n2 = {"AAPL": ("CRISIS", 0.9)}  # tuple form
    r = _check(cur, tgt, n2)
    a = [x for x in r["alerts"] if x["ticker"] == "AAPL"][0]
    assert a["ns2_regime"] == "CRISIS"


# ── urgency ──────────────────────────────────────────────────────────────
def test_urgency_urgent_band():
    # delta 60% >= 50% urgent band → URGENT
    cur = {"AAPL": 0.08, "BIL": 0.92}
    tgt = {"AAPL": 0.05, "BIL": 0.95}
    r = _check(cur, tgt, {})
    a = [x for x in r["alerts"] if x["ticker"] == "AAPL"][0]
    assert a["urgency"] == "URGENT"


def test_urgency_crisis_overweight_urgent():
    # delta 40% (<50% urgent band) but CRISIS overweight → URGENT
    cur = {"AAPL": 0.07, "BIL": 0.93}
    tgt = {"AAPL": 0.05, "BIL": 0.95}  # delta 40%
    n2 = {"AAPL": {"regime": "CRISIS"}}
    r = _check(cur, tgt, n2)
    a = [x for x in r["alerts"] if x["ticker"] == "AAPL"][0]
    assert a["urgency"] == "URGENT"


def test_urgency_trending_underweight_recommended():
    cur = {"AAPL": 0.03, "BIL": 0.97}
    tgt = {"AAPL": 0.05, "BIL": 0.95}  # delta -40%
    n2 = {"AAPL": {"regime": "TRENDING"}}
    r = _check(cur, tgt, n2)
    a = [x for x in r["alerts"] if x["ticker"] == "AAPL"][0]
    assert a["urgency"] == "RECOMMENDED"


def test_urgency_overweight_meanrev_consider():
    # delta 40% (<50 urgent), MEAN_REV overweight → CONSIDER
    cur = {"AAPL": 0.07, "BIL": 0.93}
    tgt = {"AAPL": 0.05, "BIL": 0.95}  # delta 40%
    n2 = {"AAPL": {"regime": "MEAN_REV"}}
    r = _check(cur, tgt, n2)
    a = [x for x in r["alerts"] if x["ticker"] == "AAPL"][0]
    assert a["urgency"] == "CONSIDER"


def test_urgency_monitor_fallback():
    # underweight, MEAN_REV → MONITOR
    cur = {"AAPL": 0.03, "BIL": 0.97}
    tgt = {"AAPL": 0.05, "BIL": 0.95}  # delta -40%
    n2 = {"AAPL": {"regime": "MEAN_REV"}}
    r = _check(cur, tgt, n2)
    a = [x for x in r["alerts"] if x["ticker"] == "AAPL"][0]
    assert a["urgency"] == "MONITOR"


# ── sorting & summary ────────────────────────────────────────────────────
def test_sort_urgency_desc():
    # delta 40% (<50% urgent band) so regime determines urgency
    cur = {"A": 0.07, "B": 0.07, "C": 0.07, "BIL": 0.79}
    tgt = {"A": 0.05, "B": 0.05, "C": 0.05, "BIL": 0.85}
    n2 = {"A": {"regime": "CRISIS"}, "B": {"regime": "TRENDING"},
          "C": {"regime": "MEAN_REV"}}
    r = _check(cur, tgt, n2)
    urgencies = [a["urgency"] for a in r["alerts"]]
    assert urgencies[0] == "URGENT"  # A (CRISIS overweight)
    assert "MONITOR" in urgencies   # B (TRENDING overweight)


def test_summary_no_drift():
    cur = {"AAPL": 0.05, "BIL": 0.95}
    tgt = {"AAPL": 0.05, "BIL": 0.95}
    r = _check(cur, tgt)
    assert r["summary"] == "Portfolio at target. No drift detected."


def test_summary_counts():
    cur = {"A": 0.08, "BIL": 0.92}
    tgt = {"A": 0.05, "BIL": 0.95}
    n2 = {"A": {"regime": "CRISIS"}}  # URGENT
    r = _check(cur, tgt, n2)
    assert "1 action recommended" in r["summary"]
    assert "1 position flagged" in r["summary"]
    assert "1 urgent" in r["summary"]


def test_summary_plural():
    cur = {"A": 0.08, "B": 0.08, "BIL": 0.84}
    tgt = {"A": 0.05, "B": 0.05, "BIL": 0.90}
    n2 = {"A": {"regime": "CRISIS"}, "B": {"regime": "CRISIS"}}
    r = _check(cur, tgt, n2)
    assert "2 actions recommended" in r["summary"]
    assert "2 positions flagged" in r["summary"]


def test_as_of_present():
    cur = {"A": 0.08, "BIL": 0.92}
    tgt = {"A": 0.05, "BIL": 0.95}
    assert _check(cur, tgt)["as_of"]


def test_empty_portfolio():
    r = _check({}, {})
    assert r["alerts"] == []
    assert r["summary"] == "Portfolio at target. No drift detected."


# ── event-driven trigger ─────────────────────────────────────────────────
def test_event_driven_returns_urgent_only():
    cur = {"A": 0.08, "B": 0.07, "BIL": 0.85}
    tgt = {"A": 0.05, "B": 0.05, "BIL": 0.90}
    # A delta 60% >= 50% urgent; B delta 40% < 50% → only A
    al = drift_alert.check_event_driven_drift(cur, tgt)
    assert len(al) == 1
    assert al[0]["ticker"] == "A"


def test_event_driven_none_urgent():
    cur = {"A": 0.07, "BIL": 0.93}
    tgt = {"A": 0.05, "BIL": 0.95}  # delta 40% < 50% urgent band
    assert drift_alert.check_event_driven_drift(cur, tgt) == []
