"""
Unit tests for fundamentals_history.py (network-free: mocked companyfacts).

Covers: 10-K annual extraction (flow ~365d / instant), restatement
resolution (latest filed wins per period_end), store round-trip, and
point-in-time snapshot semantics.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fundamentals_history as fh


def _fact(end, filed, val, form="10-K", start=None):
    u = {"end": end, "filed": filed, "form": form, "val": val}
    if start:
        u["start"] = start
    return u


def _companyfacts(units_map):
    return {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": units_map["net_income"]}},
                                  "RevenueFromContractWithCustomerExcludingAssessedTax":
                                  {"units": {"USD": units_map["revenue"]}},
                                  "StockholdersEquity": {"units": {"USD": units_map["equity"]}},
                                  "EarningsPerShareDiluted":
                                  {"units": {"USD/shares": units_map["eps"]}}}}}


class TestAnnualFacts(unittest.TestCase):
    def test_flow_facts_365_day_only(self):
        ug = {"NetIncomeLoss": {"units": {"USD": [
            _fact("2025-09-27", "2025-11-01", 112.0, start="2024-09-29"),   # 363d OK
            _fact("2025-06-28", "2025-08-01", 23.4, start="2025-03-30"),   # 90d skipped
        ]}}}
        got = fh._annual_facts(ug, "NetIncomeLoss")
        self.assertEqual([(e, f, v) for e, f, v in got], [("2025-09-27", "2025-11-01", 112.0)])

    def test_as_originally_reported_earliest_filed_wins(self):
        # Later 10-Ks restate prior-year comparatives; point-in-time needs the
        # value as first reported (else FY2024 'filed' lands on the FY2025
        # 10-K and the row looks unavailable at rebalance dates in between).
        ug = {"NetIncomeLoss": {"units": {"USD": [
            _fact("2025-09-27", "2025-11-01", 112.0, start="2024-09-29"),
            _fact("2025-09-27", "2026-02-01", 109.0, start="2024-09-29"),  # restated
        ]}}}
        got = fh._annual_facts(ug, "NetIncomeLoss")
        self.assertEqual(got, [("2025-09-27", "2025-11-01", 112.0)])

    def test_comparative_in_next_10k_does_not_override(self):
        # FY2024 value appears again as a comparative in the FY2025 10-K
        # (filed 2025-10-31) — must NOT become the FY2024 row's value.
        ug = {"NetIncomeLoss": {"units": {"USD": [
            _fact("2024-09-28", "2024-10-31", 97.0, start="2023-09-30"),
            _fact("2024-09-28", "2025-10-31", 96.5, start="2023-09-30"),  # comparative
        ]}}}
        got = fh._annual_facts(ug, "NetIncomeLoss")
        self.assertEqual(got, [("2024-09-28", "2024-10-31", 97.0)])

    def test_instant_facts_kept(self):
        ug = {"StockholdersEquity": {"units": {"USD": [
            _fact("2025-09-27", "2025-11-01", 62000.0),   # instant: no start
        ]}}}
        got = fh._annual_facts(ug, "StockholdersEquity")
        self.assertEqual(got, [("2025-09-27", "2025-11-01", 62000.0)])

    def test_20f_annual_accepted(self):
        # ADRs (BABA/TSM/BHP) file Form 20-F — same ~365d annual facts, USD
        ug = {"NetIncomeLoss": {"units": {"USD": [
            _fact("2026-03-31", "2026-06-15", 15.0, form="20-F",
                  start="2025-04-01"),   # 365d annual OK
            _fact("2026-06-30", "2026-08-14", 4.0, form="6-K",
                  start="2026-04-01"),   # 90d 6-K quarterly — skipped
        ]}}}
        got = fh._annual_facts(ug, "NetIncomeLoss")
        self.assertEqual([(e, f, v) for e, f, v in got],
                         [("2026-03-31", "2026-06-15", 15.0)])


class TestExtractTicker(unittest.TestCase):
    @patch.object(fh, "_fetch_companyfacts")
    def test_extract_merges_metrics_by_period(self, mock_fetch):
        mock_fetch.return_value = _companyfacts({
            "net_income": [_fact("2025-09-27", "2025-11-01", 112.0, start="2024-09-29")],
            "revenue": [_fact("2025-09-27", "2025-11-01", 400.0, start="2024-09-29")],
            "equity": [_fact("2025-09-27", "2025-11-01", 62000.0)],
            "eps": [_fact("2025-09-27", "2025-11-01", 7.46, start="2024-09-29")],
        })
        rows = fh.extract_ticker("0000320193")
        self.assertIn("2025-09-27", rows)
        r = rows["2025-09-27"]
        self.assertEqual(r["net_income"], 112.0)
        self.assertEqual(r["revenue"], 400.0)
        self.assertEqual(r["total_equity"], 62000.0)
        self.assertEqual(r["eps_diluted"], 7.46)
        self.assertEqual(r["filed"], "2025-11-01")


class TestStoreAndQuery(unittest.TestCase):
    def setUp(self):
        fh.DB_PATH = os.path.join(os.path.dirname(__file__), "data", "test_hist.db")
        if os.path.exists(fh.DB_PATH):
            os.remove(fh.DB_PATH)

    def tearDown(self):
        if os.path.exists(fh.DB_PATH):
            os.remove(fh.DB_PATH)

    @patch.object(fh, "extract_ticker")
    def test_store_then_point_in_time(self, mock_extract):
        mock_extract.return_value = {
            "2025-09-27": {"filed": "2025-11-01", "net_income": 112.0,
                           "revenue": 400.0},
            "2024-09-28": {"filed": "2024-11-01", "net_income": 97.0,
                           "revenue": 391.0},
        }
        n, status = fh.store_ticker("AAPL", "0000320193")
        self.assertEqual((n, status), (2, "stored"))

        # as_of before the FY2025 filing -> only FY2024 visible
        snap = fh.get_snapshot("AAPL", "2025-10-01")
        self.assertEqual(snap["period_end"], "2024-09-28")
        self.assertEqual(snap["net_income"], 97.0)

        # as_of after the FY2025 filing -> latest is FY2025
        snap = fh.get_snapshot("AAPL", "2025-11-15")
        self.assertEqual(snap["period_end"], "2025-09-27")
        self.assertEqual(snap["net_income"], 112.0)

        # as_of before any filing -> None
        self.assertIsNone(fh.get_snapshot("AAPL", "2024-01-01"))

    @patch.object(fh, "extract_ticker")
    def test_cached_skip_and_force(self, mock_extract):
        mock_extract.return_value = {"2025-09-27": {"filed": "2025-11-01",
                                                    "net_income": 112.0}}
        fh.store_ticker("AAPL", "0000320193")
        n, status = fh.store_ticker("AAPL", "0000320193")   # cached: no refetch
        self.assertEqual((n, status), (0, "cached"))
        mock_extract.assert_called_once()

        mock_extract.return_value = {"2025-09-27": {"filed": "2025-11-01",
                                                    "net_income": 999.0}}
        n, status = fh.store_ticker("AAPL", "0000320193", force=True)
        self.assertEqual((n, status), (1, "stored"))
        self.assertEqual(fh.get_snapshot("AAPL", "2026-01-01")["net_income"], 999.0)

    @patch.object(fh, "extract_ticker")
    def test_history_oldest_first(self, mock_extract):
        mock_extract.return_value = {
            "2025-09-27": {"filed": "2025-11-01", "net_income": 112.0},
            "2024-09-28": {"filed": "2024-11-01", "net_income": 97.0},
        }
        fh.store_ticker("AAPL", "0000320193")
        h = fh.history("AAPL")
        self.assertEqual([r["period_end"] for r in h],
                         ["2024-09-28", "2025-09-27"])


class TestPrices(unittest.TestCase):
    def setUp(self):
        fh.DB_PATH = os.path.join(os.path.dirname(__file__), "data", "test_hist.db")
        if os.path.exists(fh.DB_PATH):
            os.remove(fh.DB_PATH)

    def tearDown(self):
        if os.path.exists(fh.DB_PATH):
            os.remove(fh.DB_PATH)

    def test_store_and_price_on(self):
        fh.store_prices("AAPL", {"2025-01-02": 100.0, "2025-01-15": 105.0,
                                 "2025-02-01": 110.0})
        self.assertEqual(fh.price_on("AAPL", "2025-01-10"), 100.0)   # on/before
        self.assertEqual(fh.price_on("AAPL", "2025-01-15"), 105.0)   # exact
        self.assertEqual(fh.price_on("AAPL", "2025-03-01"), 110.0)   # last
        self.assertIsNone(fh.price_on("AAPL", "2024-12-31"))         # before any
        self.assertTrue(fh.has_prices("AAPL"))
        self.assertFalse(fh.has_prices("MSFT"))

    def test_upsert_overwrites(self):
        fh.store_prices("AAPL", {"2025-01-02": 100.0})
        fh.store_prices("AAPL", {"2025-01-02": 99.0, "2025-01-03": 101.0})
        self.assertEqual(fh.price_on("AAPL", "2025-01-02"), 99.0)
        self.assertEqual(fh.price_on("AAPL", "2025-01-04"), 101.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
