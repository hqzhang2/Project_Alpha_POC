"""Tests for NS-7 ns7_walkforward.py — small-window sanity on a fixture store.

Verifies the harness runs end-to-end on controlled data: league probation →
promotion, momentum ranking, monthly returns, annual aggregation, gate shape.

Run: python3 -m pytest NS-7_QA/tests/test_walkforward.py -q
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
import ns7_walkforward as wf
import store


@pytest.fixture()
def fx(tmp_path, monkeypatch):
    """Fixture A_T store: AAA (strong mom) + DDD (weak mom) eligible,
    BBB negative EPS (vetoed), CCC tiny cap (not eligible)."""
    at_db = tmp_path / "at" / "fundamentals_hist.db"
    at_db.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "AT_FUNDAMENTALS_DB", at_db)

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
    for t, eps, cfo, shares in [("AAA", 6.0, 100e9, 1e10),
                                ("DDD", 3.0, 60e9, 1e10),
                                ("BBB", -1.0, 50e9, 1e10),
                                ("CCC", 2.0, 30e9, 1e6)]:
        conn.execute(
            "INSERT INTO annual VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (t, "0", "2015-12-31", "2016-02-01", 1e11, 1e10, 1e10, 1e10, eps,
             1e11, 1e10, 5e10, 0, 1e10, 1e11, shares, 1e10, 1e9, 5e9,
             cfo, 2e9))
    dates = pd.bdate_range("2015-01-01", "2016-12-31")
    for t, slope in [("AAA", 0.20), ("DDD", 0.02), ("BBB", 0.10), ("CCC", 0.05)]:
        rows = [(t, d.strftime("%Y-%m-%d"), float(100 + j * slope))
                for j, d in enumerate(dates)]
        conn.executemany("INSERT INTO prices VALUES (?,?,?)", rows)
    conn.commit()
    conn.close()

    # NS-7 volume store → temp (harness doesn't need it, but keep clean).
    monkeypatch.setattr(store, "DATA_DIR", tmp_path / "ns7")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "ns7" / "ns7.db")

    membership = {"current": ["AAA", "BBB", "DDD"], "changes": []}
    return {"as_of": "2016-10-31", "membership": membership, "at_db": at_db}


def _run(fx, monkeypatch, start="2016-06-30", end="2016-10-31"):
    prices = wf.load_prices(config.AT_FUNDAMENTALS_DB)
    annual = wf.load_annual(config.AT_FUNDAMENTALS_DB)
    facts = wf.Facts(prices, annual, fx["membership"])
    # Flat SPY (calibration only — returns 0).
    spy = ([d.strftime("%Y-%m-%d") for d in pd.bdate_range("2015-01-01", "2016-12-31")],
           [100.0] * 520)
    # rebalance_months=1: these tests exercise the mechanism; the production
    # cadence (config.WF_REBALANCE_MONTHS=3) is tested by the full walk.
    return wf.simulate(start, end, facts, warmup_start="2016-03-01", spy=spy,
                       rebalance_months=1)


def test_load_and_facts_point_in_time(fx):
    prices = wf.load_prices(config.AT_FUNDAMENTALS_DB)
    annual = wf.load_annual(config.AT_FUNDAMENTALS_DB)
    facts = wf.Facts(prices, annual, fx["membership"])
    assert len(prices) == 4 and len(annual) == 4
    # Filed 2016-02-01: snapshot unavailable before that.
    assert facts.snapshot_on("AAA", "2016-01-15") is None
    assert facts.snapshot_on("AAA", "2016-03-15")["eps_diluted"] == 6.0
    assert facts.price_on("AAA", "2016-06-30") > 100


def test_simulate_small_window(fx, monkeypatch):
    results = _run(fx, monkeypatch)
    assert results["window"]["rebalances"] == 5
    assert len(results["monthly"]) == 4
    # First rebalance (2016-06-30) picks the two eligible SP500 names (AAA,
    # DDD); BBB is SP500 → Major at the league level but vetoed at pick time
    # (negative EPS); CCC never tracked.
    first = results["monthly"][0]
    assert set(first["picks"]) == {"AAA", "DDD"}
    # Strategy return over the month = equal-weight mean of the two names.
    assert first["strategy"] is not None and abs(first["strategy"]) < 0.5
    assert len(results["yearly"]) == 1
    y = results["yearly"][0]
    assert y["year"] == "2016"
    for k in ("strategy", "universe", "spy", "excess_vs_universe"):
        assert k in y
    assert results["gate"]["G4_concentration_ok"] is True
    assert results["drawdown"]["strategy_mdd"] <= 0.0
    # League dynamics: SP500 members are Major from day one (no probation).
    assert results["monthly"][0]["major_count"] == 3


def test_simulate_no_major_early(tmp_path, monkeypatch):
    # Non-SP500 $50-75B name: fresh Minor → 90-day probation → NO picks in a
    # window entirely inside the probation. Isolated DB (only XXX exists).
    at_db = tmp_path / "at" / "fundamentals_hist.db"
    at_db.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "AT_FUNDAMENTALS_DB", at_db)
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
    conn.execute(
        "INSERT INTO annual VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("XXX", "0", "2015-12-31", "2016-02-01", 1e11, 1e10, 1e10, 1e10, 2.0,
         1e11, 1e10, 5e10, 0, 1e10, 1e11, 4.6e8, 1e10, 1e9, 5e9, 30e9, 2e9))
    dates = pd.bdate_range("2015-01-01", "2016-12-31")
    conn.executemany("INSERT INTO prices VALUES (?,?,?)",
                     [("XXX", d.strftime("%Y-%m-%d"), 100.0 + j * 0.02)
                      for j, d in enumerate(dates)])
    conn.commit()
    conn.close()
    membership = {"current": [], "changes": []}      # XXX not in SP500
    facts = wf.Facts(wf.load_prices(at_db), wf.load_annual(at_db), membership)
    results = wf.simulate("2016-04-30", "2016-05-31", facts,
                          warmup_start="2016-03-01", spy=([], []),
                          rebalance_months=1)
    assert results["monthly"][0]["picks"] == []
    assert results["monthly"][0]["strategy"] == 0.0
    assert results["gate"]["G1_pass"] is False   # 0 excess years in 0 full years
