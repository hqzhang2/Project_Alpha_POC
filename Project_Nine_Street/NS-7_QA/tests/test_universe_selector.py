"""Tests for NS-7 universe league state machine + selector logic.

The league transitions are the heart of the design — these are the tests that
prove a transient dip doesn't churn the book, a 90-day grace holds, and data
survives demotion (paused, not deleted).

Run: python3 -m pytest NS-7_QA/tests/ -q
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import universe
import selector
import store


# ── Eligibility ─────────────────────────────────────────────────────────
def test_large_or_indexed():
    assert universe.is_large_and_indexed(60e9, False) is True   # > $50B
    assert universe.is_large_and_indexed(10e9, True) is True    # in SP500
    assert universe.is_large_and_indexed(10e9, False) is False  # neither


def test_liquid():
    assert universe.is_liquid(150_000.0) is True
    assert universe.is_liquid(99_999.0) is False                # > 100K, strict


def test_quality_missing_is_not_proven():
    assert universe.is_quality(None, 5.0) is False
    assert universe.is_quality(1.0, None) is False
    assert universe.is_quality(1.0, 5.0) is True
    assert universe.is_quality(-1.0, 5.0) is False


def test_meets_all_criteria():
    good = {"market_cap": 60e9, "in_sp500": False, "avg_daily_volume": 200_000,
            "eps_ttm": 1.0, "cfo_ttm": 5.0}
    assert universe.meets_all_criteria(good) is True
    bad = dict(good, avg_daily_volume=50_000)   # illiquid
    assert universe.meets_all_criteria(bad) is False


# ── League transitions (§3.2 rules) ─────────────────────────────────────
def test_major_demotes_immediately_on_failure():
    assert universe.transition("major", False, 0, 0, True) == "minor"


def test_major_stays_on_compliance():
    assert universe.transition("major", True, 0, 0, True) == "major"


def test_minor_promotes_after_grace():
    grace = config.GRACE_PERIOD_DAYS
    # 89 compliant days: still minor
    assert universe.transition("minor", True, grace - 1, 0, True) == "minor"
    # 90 compliant days: promote
    assert universe.transition("minor", True, grace, 0, True) == "major"


def test_minor_expires_after_grace_noncompliance():
    grace = config.GRACE_PERIOD_DAYS
    assert universe.transition("minor", False, 0, grace - 1, True) == "minor"
    assert universe.transition("minor", False, 0, grace, True) == "removed"


def test_removed_stays_removed():
    assert universe.transition("removed", True, 0, 0, True) == "removed"


def test_tenure_counters_reset_on_flip():
    # compliant -> reset noncompliant; noncompliant -> reset compliant
    assert universe.advance_tenure("minor", True, 5, 0) == (6, 0)
    assert universe.advance_tenure("minor", False, 5, 0) == (0, 1)


def test_only_major_is_assessable():
    assert universe.is_assessable("major") is True
    assert universe.is_assessable("minor") is False
    assert universe.is_assessable("removed") is False


# ── Momentum signal ─────────────────────────────────────────────────────
def test_skip_month_momentum_short_series_none():
    assert selector.skip_month_momentum([100.0] * 10) is None


def test_skip_month_momentum_flat_is_zero():
    closes = [100.0] * (config.MOMENTUM_LOOKBACK_DAYS + 5)
    assert selector.skip_month_momentum(closes) == 0.0


def test_skip_month_momentum_rising_is_positive():
    n = config.MOMENTUM_LOOKBACK_DAYS + 5
    closes = [100.0 + i for i in range(n)]
    mom = selector.skip_month_momentum(closes)
    assert mom is not None and mom > 0


def test_rank_major_quality_veto_excludes_negative_eps():
    n = config.MOMENTUM_LOOKBACK_DAYS + 5
    rising = [100.0 + i for i in range(n)]
    flat = [100.0] * n
    prices = {"AAA": rising, "BBB": flat}
    facts = {
        "AAA": {"eps_ttm": 1.0, "cfo_ttm": 5.0},
        "BBB": {"eps_ttm": -1.0, "cfo_ttm": 5.0},   # fails quality veto
    }
    ranked = selector.rank_major(prices, facts)
    tickers = [r["ticker"] for r in ranked]
    assert "AAA" in tickers
    assert "BBB" not in tickers           # vetoed despite being scored


def test_rank_major_caps_at_top_n():
    n = config.MOMENTUM_LOOKBACK_DAYS + 5
    prices = {}
    facts = {}
    for i in range(config.TOP_N + 10):
        t = f"T{i:02d}"
        prices[t] = [100.0 + i + (j * 0.01) for j in range(n)]
        facts[t] = {"eps_ttm": 1.0, "cfo_ttm": 1.0}
    ranked = selector.rank_major(prices, facts)
    assert len(ranked) == config.TOP_N


def test_concentration_guardrail():
    # 20 equal weights = effective N 20 (>= 15), max 5% (< 8%): ok
    w = {f"T{i}": 0.05 for i in range(20)}
    assert selector.concentration_ok(w) is True
    # one name at 10% violates the 8% cap
    bad = dict(w)
    bad["T0"] = 0.10
    assert selector.concentration_ok(bad) is False
    # 5 names at 20% = effective N 5 (< 15): fails even with no single > 8%? no —
    # 20% > 8% so it fails on the per-name cap too; test effective-N separately
    assert selector.effective_n({"A": 0.2, "B": 0.2, "C": 0.2,
                                 "D": 0.2, "E": 0.2}) < config.MIN_EFFECTIVE_N


# ── Anti-churn turnover band (G5) ───────────────────────────────────────
def _ranked(n):
    return [{"ticker": f"T{i:02d}", "momentum": round(1.0 - i * 0.01, 6),
             "rank": i + 1} for i in range(n)]


def test_turnover_band_keeps_held_name_inside_band():
    ranked = _ranked(40)                          # 40 scored names
    # T25 (rank 26) is held and still within top-30 (TOP_N 20 + band 10).
    held = {"T25"}
    picks = selector.apply_turnover_band(ranked, held)
    assert len(picks) == config.TOP_N
    assert "T25" in {p["ticker"] for p in picks}  # kept despite rank 26


def test_turnover_band_drops_name_outside_band():
    ranked = _ranked(40)
    # T35 (rank 36) is held but outside top-30 → dropped.
    held = {"T35"}
    picks = selector.apply_turnover_band(ranked, held)
    assert "T35" not in {p["ticker"] for p in picks}


def test_turnover_band_fills_with_newcomers():
    ranked = _ranked(40)
    # Hold 5 names ranked 16-20 (inside band) → they stay, rest fills up.
    held = {f"T{i:02d}" for i in range(15, 20)}
    picks = selector.apply_turnover_band(ranked, held)
    assert len(picks) == config.TOP_N
    kept = {p["ticker"] for p in picks}
    assert held <= kept                     # all 5 held names preserved
    # Top names all present; total capped at 20.
    assert kept == {f"T{i:02d}" for i in range(20)}


# ── Store persistence (temp dir) ────────────────────────────────────────
def test_store_upsert_and_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "ns7.db")
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    store.init_db()
    store.upsert_league("AAPL", "major", 30, 0, "2026-01-01", "2026-08-01")
    store.upsert_league("ZZZZ", "minor", 5, 3, "2026-01-01", "2026-08-01")
    assert store.get_league("aapl")["league"] == "major"   # case-insensitive
    counts = store.league_counts()
    assert counts.get("major") == 1
    assert counts.get("minor") == 1
