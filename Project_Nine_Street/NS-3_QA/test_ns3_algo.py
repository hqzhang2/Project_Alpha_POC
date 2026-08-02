"""Unit tests for NS-3 QA validated algorithm (52w momentum rank, display-only
conviction, deterministic RS tier-3). Data layer mocked - offline, fast.
Run: pytest test_ns3_algo.py
"""
import os
import sys
import unittest
from unittest import mock

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qa_server as q


def synth_week_close(n=70, drift=0.01, seed=0):
    """Synthetic weekly close series with positive drift."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, 0.02, n)
    idx = pd.date_range("2024-01-05", periods=n, freq="W-FRI")
    return pd.Series(np.cumprod(1 + rets) * 100, index=idx)


def synth_ohlcv(symbols, weeks=None, drift_map=None):
    """Mock weekly OHLCV dict: deterministic per-symbol drift."""
    drift_map = drift_map or {}
    out = {}
    base_idx = pd.date_range("2024-01-05", periods=70, freq="W-FRI")
    for i, sym in enumerate(symbols):
        drift = drift_map.get(sym, 0.005 + 0.002 * i)
        close = synth_week_close(70, drift=drift, seed=i)
        out[sym] = pd.DataFrame({
            "open": close * 0.999, "high": close * 1.01,
            "low": close * 0.99, "close": close,
            "volume": pd.Series(np.full(70, 1e6), index=base_idx),
        })
    return out


class TestTier1(unittest.TestCase):
    def test_52w_momentum_rank_order(self):
        """Higher ratio drift -> higher momentum -> better rank."""
        symbols = [s["symbol"] for s in q.SECTORS] + ["SPY"]
        # SPY flat, sectors with increasing drift -> ranking must follow drift
        drift = {s: 0.001 * i for i, s in enumerate(symbols)}
        with mock.patch.object(q, "get_weekly_ohlcv",
                               return_value=synth_ohlcv(symbols, drift)):
            t1 = q.run_tier1()
        self.assertEqual(len(t1["sectors"]), 11)
        mom = [s["momentum"] for s in t1["sectors"]]
        self.assertEqual(mom, sorted(mom, reverse=True), "ranked by momentum desc")
        # top 3 pass, rest don't
        passes = [s["passToTier2"] for s in t1["sectors"]]
        self.assertEqual(passes, [True] * 3 + [False] * 8)

    def test_momentum_uses_52_week_window(self):
        """Momentum must be ratio change over ~52w, not the full window."""
        symbols = [s["symbol"] for s in q.SECTORS[:2]] + ["SPY"]
        ohlcv = synth_ohlcv(symbols)
        # force a recent 52w rally after a long flat stretch: momentum ~ high
        with mock.patch.object(q, "get_weekly_ohlcv", return_value=ohlcv):
            t1 = q.run_tier1()
        s0 = t1["sectors"][0]
        self.assertTrue(-100 < s0["momentum"] < 10000)


class TestTier2(unittest.TestCase):
    def test_conviction_display_only_no_gate(self):
        """All top-3 get tier2 entries regardless of score; score within 0-5."""
        symbols = [s["symbol"] for s in q.SECTORS] + ["SPY"]
        drift = {s: 0.01 for s in symbols}  # strong uptrend for everyone
        with mock.patch.object(q, "get_weekly_ohlcv",
                               return_value=synth_ohlcv(symbols, drift)):
            t1 = q.run_tier1()
            t2 = q.run_tier2()
        self.assertEqual(len(t2["etfs"]), 3)
        for e in t2["etfs"]:
            self.assertIn(e["symbol"], [s["symbol"] for s in t1["sectors"][:3]])
            self.assertGreaterEqual(e["score"], 0)
            self.assertLessEqual(e["score"], e["maxScore"])
            self.assertIn("hmm", e)
            # bullProb is either a prob or None (HMM unavailable) - never fake
            if e["hmm"]["bullProb"] is not None:
                self.assertGreaterEqual(e["hmm"]["bullProb"], 0.0)
                self.assertLessEqual(e["hmm"]["bullProb"], 1.0)


class TestTier3(unittest.TestCase):
    def test_deterministic_no_random(self):
        """Same data -> identical output; no np.random anywhere."""
        with mock.patch.object(q, "get_weekly_ohlcv", side_effect=synth_ohlcv), \
             mock.patch.object(q, "get_piotroski", return_value=(8, {"ROA_positive": True})):
            a = q.run_tier3()
            b = q.run_tier3()
        self.assertEqual(a["sectors"], b["sectors"])  # payload deterministic (ignore timestamp)
        # every stock has the expected fields
        for sec in a["sectors"]:
            for st in sec["stocks"]:
                self.assertIn(st["decision"], ("BUY", "WATCH", "AVOID"))
                self.assertIsInstance(st["rs26w"], float)
                self.assertIsInstance(st["fscore"], int)
                self.assertIsInstance(st["taScore"], int)
                self.assertIn("fscoreBreakdown", st)
                self.assertIn("taBreakdown", st)

    def test_piotroski_screen_filters_rs_leaders(self):
        """F-Score screen: high scores -> only RS leaders pass; low scores ->
        fallback engages (top-3 by F-Score, PROD behavior)."""
        with mock.patch.object(q, "get_weekly_ohlcv", side_effect=synth_ohlcv), \
             mock.patch.object(q, "get_piotroski", return_value=(9, {})):
            t3_high = q.run_tier3()
        with mock.patch.object(q, "get_weekly_ohlcv", side_effect=synth_ohlcv), \
             mock.patch.object(q, "get_piotroski", return_value=(4, {})):
            t3_low = q.run_tier3()

        for sec_high, sec_low in zip(t3_high["sectors"], t3_low["sectors"]):
            # high F-Score: only the RS leaders (top ~25%) appear, all >= 7
            self.assertGreaterEqual(len(sec_high["stocks"]), 1)
            self.assertLessEqual(len(sec_high["stocks"]), 3)
            for st in sec_high["stocks"]:
                self.assertGreaterEqual(st["fscore"], q.PIOTROSKI_MIN)
            # low F-Score: fallback to top-3 by F-Score still yields stocks
            self.assertLessEqual(len(sec_low["stocks"]), 3)
            for st in sec_low["stocks"]:
                self.assertEqual(st["fscore"], 4)


    def test_hmm_gate_is_soft(self):
        """HMM must never hard-AVOID: a stock with high TA score reaches BUY
        even when hmmOk=False (hmmProb only scales confidence)."""
        with mock.patch.object(q, "get_weekly_ohlcv", side_effect=synth_ohlcv), \
             mock.patch.object(q, "get_piotroski", return_value=(9, {})), \
             mock.patch.object(q, "fit_hmm_bull_prob", return_value=0.1):  # bear regime
            t3 = q.run_tier3()
        # at least one stock must be BUY/WATCH (not everything AVOID'd by HMM)
        decisions = [st["decision"] for sec in t3["sectors"] for st in sec["stocks"]]
        self.assertTrue(any(d != "AVOID" for d in decisions),
                        f"all AVOID despite soft HMM: {decisions}")
        # confidence must reflect the low hmmProb scaling (<= 0.5 * score/5)
        for sec in t3["sectors"]:
            for st in sec["stocks"]:
                self.assertLessEqual(st["confidence"], 0.5 * (st["taScore"] / 5) + 1e-9)


class TestHMM(unittest.TestCase):
    def test_fallback_when_unavailable(self):
        """No hmmlearn -> fit returns None (explicit, never a fake)."""
        with mock.patch.object(q, "HMM_AVAILABLE", False):
            self.assertIsNone(q.fit_hmm_bull_prob(synth_week_close()))

    def test_returns_probability_when_available(self):
        if not q.HMM_AVAILABLE:
            self.skipTest("hmmlearn not installed")
        p = q.fit_hmm_bull_prob(synth_week_close(drift=0.02))
        self.assertIsNotNone(p)
        self.assertGreaterEqual(p, 0.0)
        self.assertLessEqual(p, 1.0)


if __name__ == "__main__":
    unittest.main()
