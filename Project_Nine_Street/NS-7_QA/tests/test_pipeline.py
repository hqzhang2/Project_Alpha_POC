"""Tests for NS-7 pipeline.py — data pipeline, league orchestration, selection feed.

The pipeline reads A_T's store read-only; these tests point it at a fixture DB
(fake annual + prices tables) and redirect NS-7's own store to a temp dir.

Run: python3 -m pytest NS-7_QA/tests/test_pipeline.py -q
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import pipeline
import selector
import store
import universe


# ── Fixture: fake A_T store + temp NS-7 store ───────────────────────────
@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Redirect NS-7 store + A_T paths to temp; seed a fake A_T DB."""
    # NS-7 store → temp
    monkeypatch.setattr(store, "DATA_DIR", tmp_path / "ns7")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "ns7" / "ns7.db")
    store.init_db()

    # A_T paths → temp
    at_db = tmp_path / "at" / "fundamentals_hist.db"
    at_db.parent.mkdir(parents=True, exist_ok=True)
    sp500 = tmp_path / "at" / "sp500.json"
    monkeypatch.setattr(config, "AT_FUNDAMENTALS_DB", at_db)
    monkeypatch.setattr(config, "AT_SP500_CACHE", sp500)

    conn = sqlite3.connect(str(at_db))
    conn.execute("""CREATE TABLE annual (
        ticker TEXT, cik TEXT, period_end TEXT, filed TEXT,
        revenue REAL, gross_profit REAL, operating_income REAL,
        net_income REAL, eps_diluted REAL, current_assets REAL,
        current_liabilities REAL, total_liabilities REAL,
        short_term_debt REAL, long_term_debt REAL, total_equity REAL,
        shares_outstanding REAL, cash REAL, marketable_securities REAL,
        ppe REAL, operating_cf REAL, capex REAL,
        PRIMARY KEY (ticker, period_end))""")
    conn.execute("""CREATE TABLE prices (
        ticker TEXT, date TEXT, close REAL, PRIMARY KEY (ticker, date))""")
    # Annual rows: filed dates BEFORE the rebalance date (point-in-time ok).
    # AAA: SP500 member → MAJOR immediately. BBB: SP500 member (Major at the
    # league level despite negative EPS — the quality veto applies at PICK
    # time, not league). CCC: tiny cap → never tracked. DDD: NON-SP500 with
    # $50B < cap ≤ $75B → Minor on day one (90-day clock / $75B fast-track).
    # (share counts chosen so price ~130 at series end: AAA/BBB ~$1.3T,
    #  CCC ~$130M, DDD ~$60B.)
    for t, eps, cfo, shares in [("AAA", 6.0, 100e9, 1e10),
                                ("BBB", -1.0, 50e9, 1e10),
                                ("CCC", 2.0, 30e9, 1e6),
                                ("DDD", 3.0, 40e9, 4.6e8)]:
        conn.execute(
            "INSERT INTO annual VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (t, "0", "2025-12-31", "2026-02-01", 1e11, 1e10, 1e10, 1e10, eps,
             1e11, 1e10, 5e10, 0, 1e10, 1e11, shares, 1e10, 1e9, 5e9,
             cfo, 2e9))
    # Prices: rising series through the as-of date (momentum > 0).
    dates = pd.bdate_range("2015-01-01", "2026-07-31")
    n = len(dates)
    for i, t in enumerate(["AAA", "BBB", "CCC", "DDD"]):
        rows = [(t, d.strftime("%Y-%m-%d"), float(100 + i + j * 0.01))
                for j, d in enumerate(dates)]
        conn.executemany("INSERT INTO prices VALUES (?,?,?)", rows)
    conn.commit()
    conn.close()

    sp500.write_text(json.dumps(["AAA", "BBB"]))  # CCC not in SP500

    # Seed NS-7 volume so U3 passes by default (120d coverage so the 90-day
    # league walk tests have volume at every step; tests override as needed).
    end = datetime.strptime("2026-07-31", "%Y-%m-%d")
    rows = []
    for t in ("AAA", "BBB", "CCC", "DDD"):
        rows += [(t, (end - timedelta(days=d)).strftime("%Y-%m-%d"), 200_000.0)
                 for d in range(120)]
    store.upsert_volume_many(rows)
    monkeypatch.setattr(config, "SELECTION_PATH", tmp_path / "selection.json")
    return {"as_of": "2026-07-31"}


def _seed_volume(env, monkeypatch, days=40, vol=200_000.0, tickers=("AAA", "BBB", "CCC")):
    """Seed the NS-7 volume table via a fake fetch_fn."""
    def fake_fetch(t, window):
        end = datetime.strptime(env["as_of"], "%Y-%m-%d")
        return [((end - timedelta(days=d)).strftime("%Y-%m-%d"), vol)
                for d in range(days)]
    pipeline.refresh_volumes(list(tickers), env["as_of"], fetch_fn=fake_fetch)
    return fake_fetch


# ── A_T read helpers ────────────────────────────────────────────────────
def test_snapshot_on_point_in_time(env):
    snap = pipeline.snapshot_on("AAA", "2026-07-31")
    assert snap["eps_diluted"] == 6.0
    assert pipeline.snapshot_on("AAA", "2026-01-01") is None  # filed 2026-02-01


def test_price_on_last_close(env):
    assert pipeline.price_on("AAA", "2026-07-31") is not None
    assert pipeline.price_on("AAA", "2014-01-01") is None


def test_facts_market_cap_and_quality(env):
    f = pipeline.facts_for("AAA", env["as_of"], in_sp500=True)
    # price ≈ 100-125 (rising series) × 1e10 shares → well above $50B
    assert f["market_cap"] > 50e9
    assert f["eps_ttm"] == 6.0 and f["cfo_ttm"] == 100e9
    assert pipeline.eligible(f) is True


def test_facts_negative_eps_league_eligible_but_vetoed_at_pick(env):
    # BBB is in SP500 → league-eligible (Major) despite negative EPS; the
    # quality veto applies at SELECTION time (selector.rank_major), not league.
    f = pipeline.facts_for("BBB", env["as_of"], in_sp500=True)
    assert pipeline.eligible(f) is True            # SP500 → league compliant
    assert universe.major_qualifying(f) is True
    assert universe.is_quality(f["eps_ttm"], f["cfo_ttm"]) is False  # veto fires


def test_facts_small_cap_not_eligible_unless_sp500(env):
    f_ccc = pipeline.facts_for("CCC", env["as_of"], in_sp500=False)
    assert f_ccc["market_cap"] < 50e9
    assert pipeline.eligible(f_ccc) is False       # fails U1/U2
    f_sp500 = pipeline.facts_for("CCC", env["as_of"], in_sp500=True)
    assert pipeline.eligible(f_sp500) is True      # SP500 membership covers cap


def test_facts_stale_snapshot_not_proven(env, monkeypatch):
    # Age the snapshot: period_end 2020 → >730 days by 2026-07-31.
    conn = sqlite3.connect(str(config.AT_FUNDAMENTALS_DB))
    conn.execute("UPDATE annual SET period_end='2020-12-31', filed='2021-02-01' "
                 "WHERE ticker='AAA'")
    conn.commit()
    conn.close()
    # SP500 member: league status survives (index membership is the ticket),
    # but the cap/quality facts are not proven.
    f_sp = pipeline.facts_for("AAA", env["as_of"], in_sp500=True)
    assert f_sp["eps_ttm"] is None and f_sp["cfo_ttm"] is None
    assert pipeline.eligible(f_sp) is True          # SP500 → league compliant
    # Non-SP500 with stale book: cap unknown → not eligible.
    f_non = pipeline.facts_for("AAA", env["as_of"], in_sp500=False)
    assert pipeline.eligible(f_non) is False        # cap not proven → below floor


def test_last_known_good_bridges_extraction_gap(env, monkeypatch):
    # Insert a PARTIAL newest filing (operating_cf None — extraction gap).
    # The 2026-02-01 row (all metrics) stays the last-known-good for CFO.
    conn = sqlite3.connect(str(config.AT_FUNDAMENTALS_DB))
    conn.execute(
        "INSERT INTO annual VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("AAA", "0", "2026-06-30", "2026-08-10", 1e11, 1e10, 1e10, 1e10, 7.0,
         1e11, 1e10, 5e10, 0, 1e10, 1e11, 1.1e10, 1e10, 1e9, 5e9,
         None, 2e9))  # operating_cf = None (column 20)
    conn.commit()
    conn.close()
    f = pipeline.facts_for("AAA", "2026-08-15", in_sp500=True)
    assert f["eps_ttm"] == 7.0                          # new EPS visible
    assert f["cfo_ttm"] == 100e9                        # bridged from FY2025
    assert pipeline.eligible(f) is True                 # stays eligible


def test_last_known_good_reported_negative_still_demotes(env, monkeypatch):
    # Newest filing REPORTS negative EPS → league OK (SP500) but the pick-time
    # veto still excludes it — lkg only bridges missing values, never
    # reported negatives.
    conn = sqlite3.connect(str(config.AT_FUNDAMENTALS_DB))
    conn.execute(
        "INSERT INTO annual VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("AAA", "0", "2026-06-30", "2026-08-10", 1e11, 1e10, 1e10, 1e10, -2.0,
         1e11, 1e10, 5e10, 0, 1e10, 1e11, 1.1e10, 1e10, 1e9, 5e9,
         50e9, 2e9))
    conn.commit()
    conn.close()
    f = pipeline.facts_for("AAA", "2026-08-15", in_sp500=True)
    assert f["eps_ttm"] == -2.0
    assert pipeline.eligible(f) is True              # SP500 → league compliant
    assert selector.passes_quality_veto(-2.0, 50e9) is False  # pick veto fires


# ── League orchestration (PM-corrected rules) ───────────────────────────
def _facts_map(env):
    sp500 = {"AAA", "BBB"}
    return {t: pipeline.facts_for(t, env["as_of"], t in sp500)
            for t in ("AAA", "BBB", "CCC", "DDD")}


def test_sp500_member_is_major_day_one(env):
    counts = pipeline.update_leagues(_facts_map(env), env["as_of"])
    # AAA + BBB (SP500) → Major immediately; DDD (non-SP500 $60B) → Minor;
    # CCC (tiny cap) never tracked.
    assert counts["fresh"] == 3
    assert store.get_league("AAA")["league"] == "major"
    assert store.get_league("BBB")["league"] == "major"   # even with neg EPS
    assert store.get_league("DDD")["league"] == "minor"
    assert store.get_league("CCC") is None


def test_non_sp500_90_day_promotion(env):
    # DDD: non-SP500, $60B → fresh Minor; promoted after 90 compliant days.
    d0 = datetime.strptime(env["as_of"], "%Y-%m-%d") - timedelta(days=90)

    def upd(day):
        fm = {"DDD": pipeline.facts_for("DDD", day, False)}
        pipeline.update_leagues(fm, day)

    upd(d0.strftime("%Y-%m-%d"))                     # fresh → minor cc=1
    for i in range(1, 89):                           # cc → 89, still minor
        upd((d0 + timedelta(days=i)).strftime("%Y-%m-%d"))
    assert store.get_league("DDD")["league"] == "minor"
    upd((d0 + timedelta(days=89)).strftime("%Y-%m-%d"))   # cc=90 → major
    assert store.get_league("DDD")["league"] == "major"


def test_non_sp500_fasttrack_75b_immediate(env, monkeypatch):
    # DDD with cap > $75B (shares bumped) → Major on day one.
    conn = sqlite3.connect(str(config.AT_FUNDAMENTALS_DB))
    conn.execute("UPDATE annual SET shares_outstanding=7e8 WHERE ticker='DDD'")
    conn.commit()
    conn.close()
    pipeline.update_leagues({"DDD": pipeline.facts_for("DDD", env["as_of"], False)},
                            env["as_of"])
    assert store.get_league("DDD")["league"] == "major"


def test_75b_breach_promotes_minor_immediately(env, monkeypatch):
    # DDD sits in Minor at $60B; cap breaches $75B → Major the same day.
    pipeline.update_leagues({"DDD": pipeline.facts_for("DDD", env["as_of"], False)},
                            env["as_of"])
    assert store.get_league("DDD")["league"] == "minor"
    conn = sqlite3.connect(str(config.AT_FUNDAMENTALS_DB))
    conn.execute("UPDATE annual SET shares_outstanding=7e8 WHERE ticker='DDD'")
    conn.commit()
    conn.close()
    pipeline.update_leagues({"DDD": pipeline.facts_for("DDD", env["as_of"], False)},
                            env["as_of"])
    assert store.get_league("DDD")["league"] == "major"


def test_sp500_removal_kicks_in_cap_rule(env, monkeypatch):
    # AAA (Major via SP500) leaves the index with cap ~$60B → fresh Minor.
    pipeline.update_leagues({"AAA": pipeline.facts_for("AAA", env["as_of"], True)},
                            env["as_of"])
    assert store.get_league("AAA")["league"] == "major"
    conn = sqlite3.connect(str(config.AT_FUNDAMENTALS_DB))
    conn.execute("UPDATE annual SET shares_outstanding=4.6e8 WHERE ticker='AAA'")
    conn.commit()
    conn.close()
    pipeline.update_leagues(
        {"AAA": pipeline.facts_for("AAA", env["as_of"], False)},  # left index
        env["as_of"], sp500_removed={"AAA"})
    row = store.get_league("AAA")
    assert row["league"] == "minor"          # non-SP500 rule: fresh Minor clock
    assert row["consecutive_compliant"] == 1


def test_sp500_removal_with_75b_cap_stays_major(env, monkeypatch):
    # AAA leaves SP500 but cap > $75B → still Major via fast-track.
    pipeline.update_leagues({"AAA": pipeline.facts_for("AAA", env["as_of"], True)},
                            env["as_of"])
    conn = sqlite3.connect(str(config.AT_FUNDAMENTALS_DB))
    conn.execute("UPDATE annual SET shares_outstanding=1e10 WHERE ticker='AAA'")
    conn.commit()
    conn.close()
    pipeline.update_leagues(
        {"AAA": pipeline.facts_for("AAA", env["as_of"], False)},
        env["as_of"], sp500_removed={"AAA"})
    assert store.get_league("AAA")["league"] == "major"


def test_demotion_immediate(env):
    # Major with cap ≤ $50B → demoted the same day.
    store.upsert_league("AAA", "major", 95, 0, "2026-01-01", env["as_of"])
    bad = {"AAA": {"ticker": "AAA", "in_sp500": False, "market_cap": 30e9,
                   "eps_ttm": -1.0, "cfo_ttm": 5.0,
                   "avg_daily_volume": 200_000.0, "snapshot_age_days": 10}}
    pipeline.update_leagues(bad, env["as_of"])
    row = store.get_league("AAA")
    assert row["league"] == "minor"
    assert row["consecutive_noncompliant"] == 1


def test_expiry_after_90_days_noncompliance(env):
    store.upsert_league("AAA", "minor", 0, 88, "2026-01-01", env["as_of"])
    bad = {"AAA": {"ticker": "AAA", "in_sp500": False, "market_cap": None,
                   "eps_ttm": None, "cfo_ttm": None, "avg_daily_volume": None,
                   "snapshot_age_days": 999}}
    pipeline.update_leagues(bad, env["as_of"])
    assert store.get_league("AAA")["league"] == "minor"  # nc=89 < 90
    pipeline.update_leagues(bad, env["as_of"])
    assert store.get_league("AAA")["league"] == "removed"  # nc=90


def test_readmission_as_fresh(env):
    # Removed + now SP500 → re-admitted as MAJOR (index membership is the ticket).
    store.upsert_league("AAA", "removed", 0, 90, "2026-01-01", env["as_of"])
    pipeline.update_leagues({"AAA": pipeline.facts_for("AAA", env["as_of"], True)},
                            env["as_of"])
    assert store.get_league("AAA")["league"] == "major"
    # Removed + non-SP500 $60B → re-admitted as fresh MINOR.
    store.upsert_league("DDD", "removed", 0, 90, "2026-01-01", env["as_of"])
    pipeline.update_leagues({"DDD": pipeline.facts_for("DDD", env["as_of"], False)},
                            env["as_of"])
    row = store.get_league("DDD")
    assert row["league"] == "minor"
    assert row["first_seen"] == env["as_of"]         # fresh probation restarts


# ── Volume + U3 ─────────────────────────────────────────────────────────
def test_volume_refresh_and_avg(env, monkeypatch):
    _seed_volume(env, monkeypatch)
    assert store.avg_daily_volume("AAA", env["as_of"], 20) == pytest.approx(200_000.0)
    assert store.volume_coverage("AAA")[2] == 120     # fixture seed + 40 new


def test_volume_systemic_failure_waives_u3(env, monkeypatch):
    def boom(t, window):
        raise ConnectionError("yfinance down")
    # Fresh tickers (no seeded volume) + as_of beyond coverage → stale → fetch.
    res = pipeline.refresh_volumes(["ZZZ", "YYY"], "2026-08-20", fetch_fn=boom)
    assert res["systemic_failure"] is True
    assert store.avg_daily_volume("ZZZ", "2026-08-20", 20) is None


def test_volume_per_ticker_failure_not_systemic(env, monkeypatch):
    def partial(t, window):
        if t == "AAA":
            raise ConnectionError("single ticker fail")
        return [("2026-08-20", 150_000.0)]
    res = pipeline.refresh_volumes(["AAA", "BBB"], "2026-08-20", fetch_fn=partial)
    assert res["systemic_failure"] is False
    assert store.avg_daily_volume("BBB", "2026-08-20", 1) == 150_000.0


# ── Benchmark filter (SPY & QQQ, same 126/21 window) ────────────────────
def _fake_bench(spy_mom, qqq_mom, n=300):
    """Synthetic bench series with the requested skip-month momentum."""
    dates = pd.bdate_range("2020-01-01", periods=n)
    out = {}
    for sym, mom in (("SPY", spy_mom), ("QQQ", qqq_mom)):
        closes = [100.0] * n
        # P[t-21] = closes[-21], P[t-126] = closes[-126]; set them to hit `mom`.
        closes[-config.MOMENTUM_SKIP_DAYS] = 100.0 * (1 + mom)
        closes[-config.MOMENTUM_LOOKBACK_DAYS] = 100.0
        out[sym] = [(d.strftime("%Y-%m-%d"), float(c))
                    for d, c in zip(dates, closes)]
    return out


def test_bench_momentum_computes_same_window(env, monkeypatch):
    bench = _fake_bench(0.10, 0.05)
    bm = pipeline.bench_momentum(env["as_of"], bench=bench)
    assert bm is not None
    assert bm["spy"] == pytest.approx(0.10, abs=1e-6)
    assert bm["qqq"] == pytest.approx(0.05, abs=1e-6)


def test_bench_momentum_short_series_none(env, monkeypatch):
    bench = {"SPY": _fake_bench(0.10, 0.05)["SPY"][:50],
             "QQQ": _fake_bench(0.10, 0.05)["QQQ"]}
    assert pipeline.bench_momentum(env["as_of"], bench=bench) is None


def test_selection_doc_carries_benchmark_flags(env, monkeypatch):
    # Force AAA/DDD to Major; fixture rising series has ~1% skip-month mom.
    store.upsert_league("AAA", "major", 95, 0, "2026-01-01", env["as_of"])
    store.upsert_league("DDD", "major", 95, 0, "2026-01-01", env["as_of"])
    fm = {"AAA": pipeline.facts_for("AAA", env["as_of"], True),
          "DDD": pipeline.facts_for("DDD", env["as_of"], False)}

    # Low bench (0.5%/0.2%) → the ~1% picks outperform BOTH.
    monkeypatch.setattr(pipeline, "bench_momentum",
                        lambda as_of: {"spy": 0.005, "qqq": 0.002})
    doc = pipeline.run_selection(env["as_of"], fm)
    assert doc["benchmarks"] == {"spy": 0.005, "qqq": 0.002}
    assert all(s["outperforms_benchmarks"] is True for s in doc["selections"])
    # The FULL scored list carries the same flag + rank (outperformer panel).
    assert all("outperforms_benchmarks" in s and "rank" in s
               for s in doc["scores"])
    assert any(s["outperforms_benchmarks"] for s in doc["scores"])

    # High bench (10%/5%) → picks lag BOTH → flags False.
    monkeypatch.setattr(pipeline, "bench_momentum",
                        lambda as_of: {"spy": 0.10, "qqq": 0.05})
    doc2 = pipeline.run_selection(env["as_of"], fm)
    assert all(s["outperforms_benchmarks"] is False for s in doc2["selections"])

    # Benchmark availability is fail-open: None → flags False, feed intact.
    monkeypatch.setattr(pipeline, "bench_momentum", lambda as_of: None)
    doc3 = pipeline.run_selection(env["as_of"], fm)
    assert doc3["benchmarks"] is None
    assert all(s["outperforms_benchmarks"] is False for s in doc3["selections"])
    assert len(doc3["selections"]) == 2


# ── NS-2 advisory overlay (DESIGN §4.3) ─────────────────────────────────
def test_load_ns2_signals_parses_cache(tmp_path):
    p = tmp_path / "ns2_signal_cache.json"
    p.write_text(json.dumps({"AAPL": {"signal": "HOLD LONG", "color": "#7ec8e3"},
                             "NVDA": {"signal": "FLAT", "color": "#444"},
                             "MSFT": {"signal": "NO-EDGE", "color": "#666"}}))
    sigs = pipeline.load_ns2_signals(str(p))
    assert sigs == {"AAPL": "HOLD LONG", "NVDA": "FLAT", "MSFT": "NO-EDGE"}


def test_load_ns2_signals_fail_open():
    assert pipeline.load_ns2_signals("/nonexistent/ns2.json") == {}


def test_selection_carries_ns2_advisory_flags(env, monkeypatch, tmp_path):
    # Force AAA/DDD to Major; NS-2 has no conviction on DDD, confirms AAA.
    store.upsert_league("AAA", "major", 95, 0, "2026-01-01", env["as_of"])
    store.upsert_league("DDD", "major", 95, 0, "2026-01-01", env["as_of"])
    p = tmp_path / "ns2.json"
    p.write_text(json.dumps({"AAA": {"signal": "HOLD LONG"},
                             "DDD": {"signal": "FLAT"}}))
    monkeypatch.setattr(config, "NS2_SIGNAL_PATH", p)
    monkeypatch.setattr(pipeline, "bench_momentum", lambda as_of: None)
    fm = {"AAA": pipeline.facts_for("AAA", env["as_of"], True),
          "DDD": pipeline.facts_for("DDD", env["as_of"], False)}
    doc = pipeline.run_selection(env["as_of"], fm)
    by_t = {s["ticker"]: s for s in doc["selections"]}
    assert by_t["AAA"]["ns2_signal"] == "HOLD LONG"
    assert by_t["AAA"]["ns2_advisory"] is False
    assert by_t["DDD"]["ns2_signal"] == "FLAT"
    assert by_t["DDD"]["ns2_advisory"] is True


def test_selection_ns2_neutral_when_missing(env, monkeypatch, tmp_path):
    # No NS-2 cache → picks carry no signal, no advisory (neutral).
    store.upsert_league("AAA", "major", 95, 0, "2026-01-01", env["as_of"])
    monkeypatch.setattr(config, "NS2_SIGNAL_PATH",
                        tmp_path / "missing_ns2.json")
    monkeypatch.setattr(pipeline, "bench_momentum", lambda as_of: None)
    fm = {"AAA": pipeline.facts_for("AAA", env["as_of"], True)}
    doc = pipeline.run_selection(env["as_of"], fm)
    s = doc["selections"][0]
    assert s["ns2_signal"] is None
    assert s["ns2_advisory"] is False


# ── Full refresh + selection feed ───────────────────────────────────────
def test_run_refresh_full_pipeline(env, monkeypatch):
    def fake_fetch(t, window):
        end = datetime.strptime("2026-08-20", "%Y-%m-%d")
        return [((end - timedelta(days=d)).strftime("%Y-%m-%d"), 200_000.0)
                for d in range(40)]
    summary = pipeline.run_refresh(as_of="2026-08-20", fetch_volumes=True,
                                   limit=0)
    # AAA should be the sole Major after 1 day (fresh Minor probation) — so
    # selections are empty until the 90-day promotion; assert feed shape.
    assert summary["candidates"] >= 3
    assert summary["volume"]["fetched"] >= 1
    assert store.get_meta("last_refresh") == "2026-08-20"
    assert config.SELECTION_PATH.exists()
    doc = json.loads(config.SELECTION_PATH.read_text())
    assert doc["service"] == "NS-7"
    assert doc["as_of"] == "2026-08-20"
    assert doc["selections"] == [] or all("ticker" in s for s in doc["selections"])


def test_selection_ranks_and_vetoes(env, monkeypatch):
    # Force AAA to Major directly, then run selection.
    store.upsert_league("AAA", "major", 95, 0, "2026-01-01", env["as_of"])
    store.upsert_league("BBB", "major", 95, 0, "2026-01-01", env["as_of"])
    fm = {"AAA": pipeline.facts_for("AAA", env["as_of"], True),
          "BBB": pipeline.facts_for("BBB", env["as_of"], True)}
    doc = pipeline.run_selection(env["as_of"], fm)
    tickers = [s["ticker"] for s in doc["selections"]]
    assert "AAA" in tickers
    assert "BBB" not in tickers                    # negative EPS vetoed
    assert doc["selections"][0]["rank"] == 1
    # The store row + json feed agree.
    latest = store.latest_selection()
    assert latest["payload"]["selections"][0]["ticker"] == "AAA"
