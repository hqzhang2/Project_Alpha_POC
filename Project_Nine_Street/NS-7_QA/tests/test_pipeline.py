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
    # AAA: fresh, positive — eligible. BBB: negative EPS — fails U4.
    # CCC: tiny cap (price*shares < $50B) — fails U2 unless in SP500.
    # (share counts chosen so price ~130 at series end: AAA/BBB ~$1.3T,
    #  CCC ~$130M.)
    for t, eps, cfo, shares in [("AAA", 6.0, 100e9, 1e10),
                                ("BBB", -1.0, 50e9, 1e10),
                                ("CCC", 2.0, 30e9, 1e6)]:
        conn.execute(
            "INSERT INTO annual VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (t, "0", "2025-12-31", "2026-02-01", 1e11, 1e10, 1e10, 1e10, eps,
             1e11, 1e10, 5e10, 0, 1e10, 1e11, shares, 1e10, 1e9, 5e9,
             cfo, 2e9))
    # Prices: rising series through the as-of date (momentum > 0).
    dates = pd.bdate_range("2015-01-01", "2026-07-31")
    n = len(dates)
    for i, t in enumerate(["AAA", "BBB", "CCC"]):
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
    for t in ("AAA", "BBB", "CCC"):
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


def test_facts_negative_eps_not_eligible(env):
    f = pipeline.facts_for("BBB", env["as_of"], in_sp500=True)
    assert pipeline.eligible(f) is False          # U4 veto


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
    f = pipeline.facts_for("AAA", env["as_of"], in_sp500=True)
    assert f["eps_ttm"] is None and f["cfo_ttm"] is None
    assert pipeline.eligible(f) is False           # stale book = not proven


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
    # Newest filing REPORTS negative EPS → demotes even with lkg available.
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
    assert pipeline.eligible(f) is False               # negative EPS vetoed


# ── League orchestration ────────────────────────────────────────────────
def _facts_map(env):
    sp500 = {"AAA", "BBB"}
    return {t: pipeline.facts_for(t, env["as_of"], t in sp500)
            for t in ("AAA", "BBB", "CCC")}


def test_fresh_entry_starts_minor(env):
    counts = pipeline.update_leagues(_facts_map(env), env["as_of"])
    assert counts["fresh"] == 1                    # AAA (BBB fails U4, CCC fails U2)
    row = store.get_league("AAA")
    assert row["league"] == "minor" and row["consecutive_compliant"] == 1
    assert store.get_league("BBB") is None         # never tracked


def test_promotion_after_90_days(env):
    # Walk FORWARD from 90 days before as_of: fresh Minor at day0, one
    # compliant update per day. Day 89 (cc=90) → Major.
    d0 = datetime.strptime(env["as_of"], "%Y-%m-%d") - timedelta(days=90)

    def upd(day):
        fm = {"AAA": pipeline.facts_for("AAA", day, True)}
        pipeline.update_leagues(fm, day)

    upd(d0.strftime("%Y-%m-%d"))                     # fresh → minor cc=1
    for i in range(1, 89):                           # cc → 89, still minor
        upd((d0 + timedelta(days=i)).strftime("%Y-%m-%d"))
    assert store.get_league("AAA")["league"] == "minor"
    upd((d0 + timedelta(days=89)).strftime("%Y-%m-%d"))   # cc=90 → major
    assert store.get_league("AAA")["league"] == "major"


def test_demotion_immediate(env):
    # Promote AAA to major, then break U4 (negative eps via bad snapshot?).
    # Simpler: force league state then run one non-compliant day.
    store.upsert_league("AAA", "major", 95, 0, "2026-01-01", env["as_of"])
    bad = {"AAA": {"ticker": "AAA", "in_sp500": True, "market_cap": 60e9,
                   "eps_ttm": -1.0, "cfo_ttm": 5.0,
                   "avg_daily_volume": 200_000.0, "snapshot_age_days": 10}}
    pipeline.update_leagues(bad, env["as_of"])
    row = store.get_league("AAA")
    assert row["league"] == "minor"
    assert row["consecutive_noncompliant"] == 1


def test_expiry_after_90_days_noncompliance(env):
    store.upsert_league("AAA", "minor", 0, 88, "2026-01-01", env["as_of"])
    bad = {"AAA": {"ticker": "AAA", "in_sp500": True, "market_cap": None,
                   "eps_ttm": None, "cfo_ttm": None, "avg_daily_volume": None,
                   "snapshot_age_days": 999}}
    pipeline.update_leagues(bad, env["as_of"])
    assert store.get_league("AAA")["league"] == "minor"  # nc=89 < 90
    pipeline.update_leagues(bad, env["as_of"])
    assert store.get_league("AAA")["league"] == "removed"  # nc=90


def test_readmission_as_fresh_minor(env):
    store.upsert_league("AAA", "removed", 0, 90, "2026-01-01", env["as_of"])
    pipeline.update_leagues(_facts_map(env), env["as_of"])
    row = store.get_league("AAA")
    assert row["league"] == "minor"
    assert row["first_seen"] == env["as_of"]         # fresh probation restarts
    assert row["consecutive_compliant"] == 1


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
