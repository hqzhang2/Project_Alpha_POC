"""NS-ETF unit tests — hermetic (temp sqlite, no network, no yfinance)."""
import json
import math
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config          # noqa: E402
import indicators      # noqa: E402
import overlay         # noqa: E402
import pipeline        # noqa: E402
import regime          # noqa: E402
import selector        # noqa: E402
import store           # noqa: E402


class TestWilderADX(unittest.TestCase):
    def test_insufficient_data_returns_none(self):
        self.assertIsNone(indicators.wilder_adx([1]*10, [1]*10, [1]*10, 14))

    def test_flat_series_zero(self):
        n = 40
        h = [100.0] * n
        self.assertIsNotNone(indicators.wilder_adx(h, h, [100.0] * n, 14))
        res = indicators.wilder_adx(h, h, [100.0] * n, 14)
        self.assertAlmostEqual(res["adx"], 0.0, places=6)

    def test_hand_computed_fixture(self):
        """Hand-computed 2-period Wilder ADX. Closes rise 1/bar with
        high=c+0.5, low=c-0.5 → every TR = 1.5 (close-gap included),
        every +DM = 1.0, -DM = 0.
        Wilder: first smoothed = SUM of first 2 → TR=3.0, +DM=2.0;
        recursion prev - prev/2 + cur keeps both constant → +DI = 2/3,
        -DI = 0 → DX = 100, ADX = 100. (Verifies the sum-seed + recursion
        that NS-4 PROD got wrong.)"""
        closes = [10.0 + i for i in range(7)]
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        res = indicators.wilder_adx(highs, lows, closes, 2)
        self.assertAlmostEqual(res["plus_di"], 200.0 / 3.0, places=6)
        self.assertAlmostEqual(res["minus_di"], 0.0, places=6)
        self.assertAlmostEqual(res["dx"], 100.0, places=6)
        self.assertAlmostEqual(res["adx"], 100.0, places=6)

    def test_downtrend_minus_di_dominates(self):
        closes = [16.0 - 0.5 * i for i in range(7)]
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        res = indicators.wilder_adx(highs, lows, closes, 2)
        # TR = 1.0, -DM = 0.5 → -DI = 50, +DI = 0, DX = 100
        self.assertAlmostEqual(res["plus_di"], 0.0, places=6)
        self.assertAlmostEqual(res["minus_di"], 50.0, places=6)
        self.assertAlmostEqual(res["dx"], 100.0, places=6)


class TestIndicators(unittest.TestCase):
    def test_rsi_all_gains_is_100(self):
        closes = [float(i) for i in range(1, 30)]
        self.assertEqual(indicators.wilder_rsi(closes, 14), 100.0)

    def test_bollinger_position_bounds(self):
        closes = [float(i) for i in range(1, 40)]
        pos = indicators.bollinger_position(closes)
        self.assertTrue(0.0 <= pos <= 1.0)

    def test_macd_shape(self):
        closes = [100 + 5 * math.sin(i / 5) + i * 0.1 for i in range(120)]
        m = indicators.macd(closes)
        self.assertIsNotNone(m)
        self.assertIn("hist", m)


class TestSelector(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nsetf-test-")
        self.db = Path(self.tmp) / "t.sqlite"
        store.init_db(self.db)
        self.conn = store._connect(self.db)
        # seed 130 days: XLK uptrend, XLU flat, XLE insufficient
        for t, drift in (("XLK", 0.002), ("XLU", 0.0), ("XLE", None)):
            if drift is None:
                continue
            rows = []
            price = 100.0
            for i in range(130):
                price *= (1 + drift)
                rows.append((f"2026-01-{(i % 28) + 1:02d}{i:03d}", price, price * 1.01, price * 0.99))
            store.upsert_prices(self.conn, t, rows)
        self.spy = selector.store_series(self.conn, "XLK")  # reuse as SPY proxy

    def tearDown(self):
        self.conn.close()

    def test_score_has_components_or_error(self):
        s = selector.score_ticker(self.conn, "XLK", self.spy)
        self.assertIn("score", s)
        self.assertIn("components", s)
        bad = selector.score_ticker(self.conn, "XLE", self.spy)
        self.assertIn("error", bad)          # surfaced, never defaulted

    def test_rank_order_and_determinism(self):
        r1 = selector.rank_sleeve(self.conn, ["XLK", "XLU"], self.spy)
        r2 = selector.rank_sleeve(self.conn, ["XLK", "XLU"], self.spy)
        self.assertEqual([x["ticker"] for x in r1], [x["ticker"] for x in r2])
        self.assertEqual(r1[0]["ticker"], "XLK")   # uptrend outranks flat

    def test_inverse_vol_weights_sum_to_one(self):
        w = selector.inverse_vol_weights(self.conn, ["XLK", "XLU"])
        self.assertAlmostEqual(sum(w.values()), 1.0, places=6)

    def test_no_randomness(self):
        import random as _r
        state = _r.getstate()
        selector.rank_sleeve(self.conn, ["XLK", "XLU"], self.spy)
        self.assertEqual(_r.getstate(), state)     # scoring must not touch RNG


class TestOverlay(unittest.TestCase):
    def test_normal_state_full_exposure(self):
        v = overlay.vix_state(15.0, 18.0)
        self.assertFalse(v["crisis"])
        self.assertEqual(v["state"], "NORMAL")

    def test_crisis_rotates_to_safe_haven(self):
        v = overlay.vix_state(30.0, 18.0)
        self.assertTrue(v["crisis"])
        final, events = overlay.apply_overlay(None, {}, {"SPY": 1.0}, v)
        self.assertTrue(set(final) <= config.CRISIS_SAFE)
        self.assertAlmostEqual(sum(final.values()), 1.0, places=6)
        self.assertTrue(any(e["type"] == "crisis_rotation" for e in events))

    def test_elevated_caps_and_floors_cash(self):
        v = overlay.vix_state(24.0, 18.0)   # ratio 1.33 → cap < 1
        self.assertFalse(v["crisis"])
        self.assertLess(v["exposure_cap"], 1.0)
        w = {"XLK": 0.5, "TLT": 0.3, "BIL": 0.2}
        final, events = overlay.apply_overlay(None, {}, w, v)
        self.assertAlmostEqual(sum(final.values()), 1.0, places=6)
        self.assertGreaterEqual(final.get("BIL", 0), 0.2)   # cash floor held

    def test_missing_vix_fails_open_normal(self):
        v = overlay.vix_state(None, None)
        self.assertFalse(v["crisis"])
        self.assertEqual(v["state"], "NORMAL")


class TestRegime(unittest.TestCase):
    def test_deterministic(self):
        series = [100 + 10 * math.sin(i / 7) + i * 0.05 for i in range(120)]
        r1 = regime.classify(momentum_series=series)
        r2 = regime.classify(momentum_series=series)
        self.assertEqual(r1, r2)

    def test_fail_open_neutral(self):
        r = regime.classify(momentum_series=None)
        self.assertEqual(r.get("regime"), "NEUTRAL")
        self.assertEqual(r.get("source"), "fail_open")

    def test_soft_scaling_never_zeroes(self):
        s = 50.0
        out = regime.scale_conviction(s, {"hmm_confidence": 0.0})
        self.assertGreater(out, 0)             # never zero
        out2 = regime.scale_conviction(s, {"hmm_confidence": None})
        self.assertEqual(out2, s)              # no confidence → unchanged


class TestPipeline(unittest.TestCase):
    def test_end_to_end_hermetic(self):
        tmp = tempfile.mkdtemp(prefix="nsetf-pipe-")
        db = str(Path(tmp) / "p.sqlite")
        old_signals = config.SIGNALS_PATH
        config.SIGNALS_PATH = Path(tmp) / "signals.json"
        try:
            def fake_fetch(tickers):
                out = {}
                for t in tickers:
                    rows, price = [], 100.0
                    drift = 0.001 if t not in ("BIL", "SHY") else 0.00002
                    for i in range(140):
                        price *= (1 + drift)
                        rows.append((f"d{i:04d}", price, price * 1.005, price * 0.995))
                    out[t] = rows
                return out
            feed = pipeline.run(fetcher=fake_fetch,
                                vix_fn=lambda: (16.0, 18.0),
                                db_path=db)
            self.assertEqual(feed["service"], "ns-etf")
            self.assertFalse(feed["crisis_mode"])
            self.assertAlmostEqual(sum(feed["weights"].values()), 1.0, delta=1e-4)
            # feed-federated tickers only (no EFA/EEM in weights)
            for t in feed["weights"]:
                self.assertIn(t, config.FEED_FED_TICKERS)
            # advisory panel present and flagged
            for r in feed["advisory_sector_ratios"]:
                if "error" not in r:
                    self.assertTrue(r["advisory_only"])
            # artifact written
            self.assertTrue(config.SIGNALS_PATH.exists())
            on_disk = json.loads(config.SIGNALS_PATH.read_text())
            self.assertEqual(on_disk["as_of"], feed["as_of"])
        finally:
            config.SIGNALS_PATH = old_signals


class TestConfigInvariants(unittest.TestCase):
    def test_intl_not_in_feed(self):
        for t in config.INTL_ETFS:
            self.assertNotIn(t, config.FEED_FED_TICKERS)
            self.assertIn(t, config.UNIVERSE)      # but still in internal book

    def test_crisis_safe_subset_of_universe(self):
        self.assertTrue(config.CRISIS_SAFE <= set(config.UNIVERSE))

    def test_ports_follow_convention(self):
        self.assertEqual(config.PORT_QA, config.PORT_PROD + 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
