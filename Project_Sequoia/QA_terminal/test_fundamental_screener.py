"""
Unit tests for fundamental_screener.py (network-free: mocked store).

Verifies verdict computation via the study scorers, agreement counting,
payload shape, and the 600s cache.
"""
import sys
import os
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fundamental_screener as fs


SNAP = {
    'period_end': '2025-09-27', 'filed': '2025-10-31',
    'revenue': 400e9, 'gross_profit': 180e9, 'operating_income': 120e9,
    'net_income': 100e9, 'eps_diluted': 6.5,
    'current_assets': 150e9, 'current_liabilities': 90e9,
    'total_liabilities': 250e9, 'short_term_debt': 5e9, 'long_term_debt': 20e9,
    'total_equity': 60e9, 'shares_outstanding': 15e9,
    'cash': 30e9, 'marketable_securities': 20e9, 'ppe': 40e9,
    'operating_cf': 110e9, 'capex': -10e9,
}
# 5 prior annuals for the Lynch 5y CAGR (eps 2.0 -> 6.5 over 5y).
# period_ends are real dates so the point-in-time filter (< FY-end) keeps them.
_HIST_ENDS = ['2020-09-26', '2021-09-25', '2022-09-24', '2023-09-30', '2024-09-28']
HIST = [{'period_end': end, 'filed': f'{end[:4]}-11-01', 'eps_diluted': eps}
        for end, eps in zip(_HIST_ENDS, [2.0, 2.6, 3.4, 4.4, 5.4])]


def _mock_store(snapshots, prices, histories=None):
    conn = MagicMock()
    # production code iterates the cursor directly (for r in conn.execute(...))
    conn.execute.return_value = [(t,) for t in snapshots]
    patchers = [
        patch.object(fs.fh, '_conn', return_value=conn),
        patch.object(fs.fh, 'get_snapshot',
                     side_effect=lambda t, d: snapshots.get(t)),
        patch.object(fs.fh, 'price_on', side_effect=lambda t, d: prices.get(t)),
        patch.object(fs.fh, 'history',
                     side_effect=lambda t: (histories or {}).get(t, [])),
    ]
    for p in patchers:
        p.start()
    return patchers


class TestScreenUniverse(unittest.TestCase):
    def tearDown(self):
        fs._cache.clear()

    def test_verdicts_and_agreement(self):
        # STRONG: cheap (pe ~4.6), profitable, low debt, growing -> Graham,
        # Lynch, Buffett pass (Greenblatt needs a real cross-section — n=2
        # caps combined rank at 0.5 < 0.80 by construction).
        # WEAK: same books at 13x the price -> only Buffett's quality gates.
        prices = {"STRONG": 30.0, "WEAK": 400.0}
        patchers = _mock_store({"STRONG": dict(SNAP), "WEAK": dict(SNAP)},
                               prices, {"STRONG": HIST, "WEAK": HIST})
        try:
            rows = {r["ticker"]: r for r in fs.screen_universe("2026-08-01")}
        finally:
            for p in patchers:
                p.stop()

        self.assertEqual(set(rows), {"STRONG", "WEAK"})
        s = rows["STRONG"]
        self.assertTrue(s["graham"]["pass"])
        self.assertTrue(s["lynch"]["pass"])        # PEG < 1
        self.assertTrue(s["buffett"]["pass"])      # ROE 1.67, FCF conv 1.0, D/E 0.42
        self.assertEqual(s["agreement"], 3)
        # payload shape
        self.assertEqual(s["snapshot_period"], "2025-09-27")
        self.assertIn("price", s)
        self.assertIn("fwd_1y", s)
        self.assertIn("score", s["graham"])
        self.assertIn("peg", s["lynch"])
        self.assertIn("roe", s["buffett"])
        # WEAK: P/E 61 -> Graham + Lynch fail; Buffett gates pass
        w = rows["WEAK"]
        self.assertFalse(w["graham"]["pass"])
        self.assertFalse(w["lynch"]["pass"])
        self.assertTrue(w["buffett"]["pass"])
        self.assertEqual(w["agreement"], 1)

    def test_greenblatt_cross_section(self):
        # 20-name universe; T00 clearly best on EBIT/EV AND ROC -> combined
        # percentile rank >= 0.80 (top-20% threshold the study validated).
        snaps, prices, hist = {}, {}, {}
        for i in range(20):
            t = f"T{i:02d}"
            s = dict(SNAP)
            s["operating_income"] = 120e9 if t == "T00" else (20e9 + i * 2e9)
            s["ppe"] = 40e9 if t == "T00" else 300e9
            snaps[t] = s
            prices[t] = 30.0 if t == "T00" else 200.0
            hist[t] = HIST
        patchers = _mock_store(snaps, prices, hist)
        try:
            rows = {r["ticker"]: r for r in fs.screen_universe("2026-08-01")}
        finally:
            for p in patchers:
                p.stop()
        self.assertTrue(rows["T00"]["greenblatt"]["pass"])
        self.assertGreater(rows["T00"]["greenblatt"]["ey"],
                           rows["T01"]["greenblatt"]["ey"])

    def test_sorted_by_agreement_desc(self):
        prices = {"STRONG": 30.0, "WEAK": 400.0}
        patchers = _mock_store({"STRONG": dict(SNAP), "WEAK": dict(SNAP)},
                               prices, {"STRONG": HIST, "WEAK": HIST})
        try:
            rows = fs.screen_universe("2026-08-01")
        finally:
            for p in patchers:
                p.stop()
        agree = [r["agreement"] for r in rows]
        self.assertEqual(agree, sorted(agree, reverse=True))

    def test_cache_and_force(self):
        prices = {"STRONG": 30.0}
        patchers = _mock_store({"STRONG": dict(SNAP)}, prices,
                               {"STRONG": HIST})
        try:
            fs.screen_universe("2026-08-01")
            calls1 = fs.fh.get_snapshot.call_count
            fs.screen_universe("2026-08-01")          # cached -> no refetch
            self.assertEqual(fs.fh.get_snapshot.call_count, calls1)
            fs.screen_universe("2026-08-01", force=True)
            self.assertGreater(fs.fh.get_snapshot.call_count, calls1)
        finally:
            for p in patchers:
                p.stop()

    def test_no_snapshot_or_price_skipped(self):
        patchers = _mock_store({"ONLY_SNAP": dict(SNAP)}, {})   # no prices
        try:
            rows = fs.screen_universe("2026-08-01")
            self.assertEqual(rows, [])
        finally:
            for p in patchers:
                p.stop()

    def test_stale_snapshot_skipped(self):
        # PBR-like: us-gaap facts end years ago — must not screen on them
        stale = dict(SNAP)
        stale["period_end"] = "2010-12-31"
        patchers = _mock_store({"STALE": stale}, {"STALE": 10.0})
        try:
            rows = fs.screen_universe("2026-08-01")
            self.assertEqual(rows, [])
        finally:
            for p in patchers:
                p.stop()

    @patch.object(fs, "derive_adr_ratio",
                  side_effect=lambda t: 8.0 if t == "STRONG" else 1.0)
    def test_adr_ratio_adjusts_per_share_math(self, _mock_ratio):
        # STRONG at $240/ADR with ratio 8 == $30 ordinary: same verdicts as
        # the $30 US case, but the display price stays the ADR price.
        prices = {"STRONG": 240.0, "WEAK": 400.0}
        patchers = _mock_store({"STRONG": dict(SNAP), "WEAK": dict(SNAP)},
                               prices, {"STRONG": HIST, "WEAK": HIST})
        try:
            rows = {r["ticker"]: r for r in fs.screen_universe("2026-08-01")}
        finally:
            for p in patchers:
                p.stop()
        s = rows["STRONG"]
        self.assertEqual(s["price"], 240.0)
        self.assertEqual(s["adr_ratio"], 8)
        self.assertTrue(s["graham"]["pass"])       # P/E 240/8/6.5 = 4.6
        self.assertTrue(s["lynch"]["pass"])        # PEG on ordinary price
        self.assertEqual(s["agreement"], 3)        # same as the $30 US case


if __name__ == "__main__":
    unittest.main(verbosity=2)
