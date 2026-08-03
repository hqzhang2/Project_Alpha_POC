"""
Hermetic tests for the OI snapshot store (option_oi_store.py).
Pure-signal tests use synthetic histories; store round-trip uses a temp DB.
"""
import datetime
import os
import tempfile

import pytest

import config
import option_oi_store as oistore

DATES = ["2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04", "2026-08-05"]


def _hist(ois, vols=None, dates=None):
    d = dates or DATES
    return [(d[i], ois[i], (vols or [0] * len(ois))[i]) for i in range(len(ois))]


# ---------------------------------------------------------------------------
# pure signal math
# ---------------------------------------------------------------------------
def test_oi_build_pct():
    h = _hist([100, 100, 110, 120, 130])
    assert oistore.oi_build_pct(h, 1) == pytest.approx(130 / 120 - 1)     # 8.33%
    assert oistore.oi_build_pct(h, 5) == pytest.approx(130 / 100 - 1)     # 30% vs anchor at cutoff
    assert oistore.oi_build_pct(_hist([100]), 5) is None                  # too short
    assert oistore.oi_build_pct(_hist([0, 0, 0, 0, 5]), 5) is None        # anchor oi == 0


def test_oi_build_pct_negative_and_zero_oi():
    h = _hist([200, 200, 200, 100, 100])
    assert oistore.oi_build_pct(h, 1) == pytest.approx(0.0)               # flat
    assert oistore.oi_build_pct(h, 5) == pytest.approx(-0.5)              # OI halved


def test_vol_percentile():
    h = _hist([0, 0, 0], vols=[10, 20, 15])
    p = oistore.vol_percentile(h)
    assert p is not None and p > 0
    assert oistore.vol_percentile(_hist([0, 0], vols=[1, 2])) is None     # < 3 points


def test_build_signals_short_history_none():
    assert oistore.build_signals(_hist([100, 100]), []) is None


def test_build_signals_divergence_true_when_flat_spot():
    h = _hist([100, 100, 100, 120, 130])                                  # +30% 5d build
    spots = [(d, 100.0) for d in DATES]
    sig = oistore.build_signals(h, spots)
    assert sig["oi_build_5d"] == pytest.approx(0.30)
    assert sig["divergence"] is True                                      # OI up, price flat


def test_build_signals_no_divergence_when_spot_moved():
    h = _hist([100, 100, 100, 120, 130])
    spots = [(d, 100.0 + 0.5 * i) for i, d in enumerate(DATES)]          # +2% spot (i=4 -> 102)
    assert oistore.build_signals(h, spots)["divergence"] is False


def test_build_signals_no_divergence_when_build_small():
    h = _hist([100, 100, 100, 105, 105])                                  # +5% build < 15%
    spots = [(d, 100.0) for d in DATES]
    assert oistore.build_signals(h, spots)["divergence"] is False


# ---------------------------------------------------------------------------
# store round-trip + idempotency (temp DB)
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    monkeypatch.setattr(oistore, "_DB_PATH", str(tmp_path / "test_oi.db"))
    oistore.init_db()
    yield
    oistore._DB_PATH = os.path.join(os.path.dirname(os.path.abspath(oistore.__file__)),
                                    "data", "option_oi.db")


def test_store_and_load_round_trip(tmp_db):
    contracts = [("2026-09-18", 100.0, "Call", 500, 1200, 2.5),
                 ("2026-09-18", 90.0, "Put", 300, 800, 1.5)]
    oistore.store_snapshot("2026-08-05", "TEST", 105.0, contracts)
    hist, spots = oistore.load_ticker_history("TEST")
    assert ("2026-09-18", 100.0, "Call") in hist
    assert hist[("2026-09-18", 100.0, "Call")] == [("2026-08-05", 500, 1200)]
    assert spots == [("2026-08-05", 105.0)]


def test_store_idempotent_same_date(tmp_db):
    c1 = [("2026-09-18", 100.0, "Call", 500, 1200, 2.5)]
    c2 = [("2026-09-18", 100.0, "Call", 900, 3000, 3.0)]   # updated values
    oistore.store_snapshot("2026-08-05", "TEST", 105.0, c1)
    oistore.store_snapshot("2026-08-05", "TEST", 106.0, c2)
    hist, spots = oistore.load_ticker_history("TEST")
    assert hist[("2026-09-18", 100.0, "Call")] == [("2026-08-05", 900, 3000)]  # replaced, not dup
    assert spots == [("2026-08-05", 106.0)]


def test_store_multiple_days_and_signals(tmp_db):
    for i, d in enumerate(DATES):
        oi = 100 + i * 10
        oistore.store_snapshot(d, "TEST", 100.0,
                               [("2026-09-18", 100.0, "Call", oi, 500, 2.0)])
    hist, spots = oistore.load_ticker_history("TEST")
    sig = oistore.build_signals(hist[("2026-09-18", 100.0, "Call")], spots)
    assert sig["oi_build_5d"] == pytest.approx(140 / 100 - 1)   # 100 -> 140 over 5 days
    assert sig["vol_pctile"] is not None
