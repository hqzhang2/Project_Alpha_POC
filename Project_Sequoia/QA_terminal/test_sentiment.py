import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Add the QA_terminal directory to the path so we can import modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import sentiment
import sentiment_db as db


def _temp_db():
    """Point sentiment_db at a throwaway DB and re-init. Returns the tmp path."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    with patch.object(db, "DB_PATH", path):
        db.init_db()
        sentiment.seed()
    return path


class TestNormalization(unittest.TestCase):
    """Money-path: sentiment axis must be +1 bullish / -1 bearish, consistently."""

    def test_passthrough_clamps(self):
        self.assertEqual(sentiment.normalize(0.6, "passthrough"), 0.6)
        self.assertEqual(sentiment.normalize(1.5, "passthrough"), 1.0)
        self.assertEqual(sentiment.normalize(-2.0, "passthrough"), -1.0)

    def test_call_share_high_ratio_is_bearish(self):
        # ratio put/call = 0.25 (lots of calls) -> near +1 (bullish)
        self.assertAlmostEqual(sentiment.normalize(0.25, "call_share"), 2 / 1.25 - 1)
        # ratio put/call = 4.0 (lots of puts) -> near -1 (bearish)
        self.assertAlmostEqual(sentiment.normalize(4.0, "call_share"), 2 / 5 - 1)
        # ratio 1.0 -> 0
        self.assertAlmostEqual(sentiment.normalize(1.0, "call_share"), 0.0)

    def test_call_share_nonpositive_returns_none(self):
        self.assertIsNone(sentiment.normalize(0, "call_share"))
        self.assertIsNone(sentiment.normalize(-1, "call_share"))

    def test_spread_100(self):
        self.assertEqual(sentiment.normalize(50, "spread_100"), 0.5)
        self.assertEqual(sentiment.normalize(-50, "spread_100"), -0.5)
        self.assertEqual(sentiment.normalize(200, "spread_100"), 1.0)  # clamped

    def test_center_50(self):
        self.assertEqual(sentiment.normalize(50, "center_50"), 0.0)
        self.assertEqual(sentiment.normalize(75, "center_50"), 0.5)
        self.assertEqual(sentiment.normalize(25, "center_50"), -0.5)

    def test_percentile_direct_and_inverted(self):
        hist = list(range(1, 101))  # 1..100
        # value 80: 79/100 below -> rank 0.79 -> sentiment 0.58
        self.assertAlmostEqual(sentiment.normalize(80, "percentile", hist), 0.58)
        # percentile_inv inverts: high VIX -> bearish (negative)
        self.assertAlmostEqual(sentiment.normalize(80, "percentile_inv", hist), -0.58)
        # extreme low -> -1 direct, +1 inverted
        self.assertAlmostEqual(sentiment.normalize(1, "percentile", hist), -1.0)
        self.assertAlmostEqual(sentiment.normalize(1, "percentile_inv", hist), 1.0)

    def test_percentile_no_history_returns_none(self):
        self.assertIsNone(sentiment.normalize(80, "percentile", []))
        self.assertIsNone(sentiment.normalize(80, "percentile", None))

    def test_null_and_bad_inputs(self):
        self.assertIsNone(sentiment.normalize(None, "passthrough"))
        self.assertIsNone(sentiment.normalize("abc", "passthrough"))
        self.assertIsNone(sentiment.normalize(0.5, None))
        self.assertIsNone(sentiment.normalize(0.5, "bogus_method"))


class TestStorage(unittest.TestCase):

    def setUp(self):
        self.db_path = _temp_db()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_upsert_idempotent_by_natural_key(self):
        with patch.object(db, "DB_PATH", self.db_path):
            r1 = db.upsert_reading("2026-08-03", "ticker", "AAPL", "put_call_oi_ratio",
                                   "oi_store", 0.82, 0.1989, count=1200)
            r2 = db.upsert_reading("2026-08-03", "ticker", "AAPL", "put_call_oi_ratio",
                                   "oi_store", 0.90, 0.1053, count=1300)
            self.assertTrue(r1 and r2)
            rows = db.query_readings()
            self.assertEqual(len(rows), 1)  # same natural key -> replaced, not duplicated
            self.assertAlmostEqual(rows[0]["value"], 0.90)
            self.assertEqual(rows[0]["count"], 1300)

    def test_market_row_upsert_idempotent_with_null_ticker(self):
        """Regression: SQLite composite PK treats NULLs as distinct, so market
        rows (ticker=None) never collided -> duplicates. The COALESCE unique
        index must make upserts idempotent for market scope too."""
        with patch.object(db, "DB_PATH", self.db_path):
            db.upsert_reading("2026-08-03", "market", None, "vix", "cboe", 17.3, -0.2, count=1)
            db.upsert_reading("2026-08-03", "market", None, "vix", "cboe", 17.8, -0.1, count=1)
            rows = db.query_readings(metric="vix")
            self.assertEqual(len(rows), 1)  # replaced, not duplicated
            self.assertAlmostEqual(rows[0]["value"], 17.8)

    def test_init_db_dedupes_legacy_duplicates(self):
        """init_db must collapse pre-index duplicate market rows (newest kept)."""
        import sqlite3
        with patch.object(db, "DB_PATH", self.db_path):
            # Simulate legacy dupes by inserting directly (bypasses the index? no —
            # index exists; so drop it first to mimic the pre-fix schema).
            conn = sqlite3.connect(self.db_path)
            conn.execute("DROP INDEX IF EXISTS idx_readings_key")
            for v in (17.3, 17.8, 17.5):
                conn.execute(
                    "INSERT INTO readings (asof_date, scope, ticker, metric, source, value, sentiment, count, recorded_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    ("2026-08-03", "market", None, "vix", "cboe", v, 0.0, 1, "t"),
                )
            conn.commit()
            conn.close()
            self.assertEqual(len(db.query_readings(metric="vix")), 3)  # dupes present
            db.init_db()  # dedup + recreate index
            rows = db.query_readings(metric="vix")
            self.assertEqual(len(rows), 1)
            self.assertAlmostEqual(rows[0]["value"], 17.5)  # newest rowid kept

    def test_query_filters(self):
        with patch.object(db, "DB_PATH", self.db_path):
            db.upsert_reading("2026-08-03", "market", None, "vix", "cboe", 17.3, -0.2, count=1)
            db.upsert_reading("2026-08-03", "ticker", "AAPL", "put_call_oi_ratio", "oi_store", 0.82, 0.2, count=1200)
            db.upsert_reading("2026-08-02", "ticker", "MSFT", "put_call_oi_ratio", "oi_store", 0.95, 0.05, count=800)
            self.assertEqual(len(db.query_readings(scope="market")), 1)
            self.assertEqual(len(db.query_readings(ticker="aapl")), 1)  # case-insensitive
            self.assertEqual(len(db.query_readings(metric="vix")), 1)
            self.assertEqual(len(db.query_readings(sources=["oi_store"])), 2)
            self.assertEqual(len(db.query_readings(days=1)), 2)  # anchored to latest date
            self.assertEqual(len(db.query_readings(days=2)), 3)
            # join supplies display metadata
            row = db.query_readings(metric="vix")[0]
            self.assertEqual(row["display_name"], "VIX")
            self.assertEqual(row["higher_is"], "bearish")

    def test_fail_open_on_missing_db(self):
        with patch.object(db, "DB_PATH", "/nonexistent/dir/xyz.db"):
            self.assertEqual(db.query_readings(), [])
            self.assertEqual(db.get_metrics(), [])
            self.assertFalse(db.upsert_reading("2026-08-03", "market", None, "vix", "cboe", 1, 0))


class TestDefinitions(unittest.TestCase):

    def test_seed_has_13_metrics_and_axis_convention(self):
        path = _temp_db()
        with patch.object(db, "DB_PATH", path):
            metrics = db.get_metrics()
            try:
                # 13 after UMich/Conf Board removal (macro, not market sentiment)
                self.assertEqual(len(metrics), 13)
                self.assertNotIn("umich", [m["metric"] for m in metrics])
                self.assertNotIn("conf_board", [m["metric"] for m in metrics])
                for m in metrics:
                    self.assertIn(m["higher_is"], ("bullish", "bearish"))
                    self.assertIn(m["scope"], ("market", "ticker"))
            finally:
                os.unlink(path)

    def test_seed_idempotent(self):
        path = _temp_db()
        with patch.object(db, "DB_PATH", path):
            sentiment.seed()
            sentiment.seed()
            self.assertEqual(len(db.get_metrics()), 13)
            os.unlink(path)

    def test_market_metrics_are_market_scope(self):
        path = _temp_db()
        with patch.object(db, "DB_PATH", path):
            vix = [m for m in db.get_metrics() if m["metric"] == "vix"][0]
            self.assertEqual(vix["scope"], "market")
            self.assertEqual(vix["normalization"], "percentile_inv")
            pcr = [m for m in db.get_metrics() if m["metric"] == "put_call_oi_ratio"][0]
            self.assertEqual(pcr["scope"], "ticker")
            self.assertEqual(pcr["normalization"], "call_share")
            os.unlink(path)


class TestUnifiedFetch(unittest.TestCase):

    def test_get_sentiment_empty_fail_open(self):
        path = _temp_db()
        with patch.object(db, "DB_PATH", path):
            try:
                self.assertEqual(sentiment.get_sentiment(), [])  # no readings yet
                self.assertEqual(len(sentiment.get_metrics()), 13)  # seeded definitions
            finally:
                os.unlink(path)

    def test_get_sentiment_joins_and_sorts(self):
        path = _temp_db()
        with patch.object(db, "DB_PATH", path):
            try:
                db.upsert_reading("2026-08-02", "ticker", "MSFT", "put_call_oi_ratio", "oi_store", 0.9, 0.05, count=800)
                db.upsert_reading("2026-08-03", "ticker", "AAPL", "put_call_oi_ratio", "oi_store", 0.8, 0.2, count=1200)
                rows = sentiment.get_sentiment(days=10)
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0]["asof_date"], "2026-08-03")  # newest first
                self.assertEqual(rows[0]["display_name"], "Put/Call OI")
            finally:
                os.unlink(path)

    def test_latest_view_one_row_per_metric(self):
        """latest=1 must return one row per (scope, ticker, metric, source),
        using each key's newest asof — lagged sources stay visible."""
        path = _temp_db()
        with patch.object(db, "DB_PATH", path):
            try:
                # vix on two dates (market scope, NULL ticker)
                db.upsert_reading("2026-08-01", "market", None, "vix", "cboe", 16.0, -0.1, count=1)
                db.upsert_reading("2026-08-06", "market", None, "vix", "cboe", 15.15, 0.79, count=1)
                # lagged naaim (old asof) + finra (old asof)
                db.upsert_reading("2026-04-29", "market", None, "naaim_exposure", "naaim", 93.79, 0.876, count=1)
                db.upsert_reading("2026-07-15", "ticker", "AAPL", "short_interest_dtc", "finra", 3.06, None, count=146547784)
                # two OI dates for AAPL
                db.upsert_reading("2026-08-02", "ticker", "AAPL", "put_call_oi_ratio", "oi_store", 0.9, 0.05, count=800)
                db.upsert_reading("2026-08-03", "ticker", "AAPL", "put_call_oi_ratio", "oi_store", 0.8, 0.2, count=1200)

                latest_rows = sentiment.get_sentiment(latest=True)
                keys = [(r["scope"], r["ticker"], r["metric"], r["source"]) for r in latest_rows]
                self.assertEqual(len(keys), len(set(keys)))  # no dup keys
                self.assertEqual(len(latest_rows), 4)  # vix, naaim, finra-aapl, oi-aapl (2 dates -> 1)
                by_metric = {r["metric"]: r for r in latest_rows}
                self.assertEqual(by_metric["vix"]["asof_date"], "2026-08-06")      # newest vix
                self.assertEqual(by_metric["naaim_exposure"]["asof_date"], "2026-04-29")  # lagged still present
                self.assertEqual(by_metric["put_call_oi_ratio"]["asof_date"], "2026-08-03")  # newest OI
                self.assertEqual(by_metric["short_interest_dtc"]["asof_date"], "2026-07-15")  # lagged present
            finally:
                os.unlink(path)


class TestRoutes(unittest.TestCase):

    def test_routes_declared(self):
        self.assertIn("/api/sentiment", sentiment.ROUTES)
        self.assertIn("/api/sentiment/metrics", sentiment.ROUTES)
        self.assertIn("/api/sentiment/providers", sentiment.ROUTES)
        self.assertEqual(sentiment.ROUTES["/api/sentiment"], "handle_sentiment")


class TestPcRatio(unittest.TestCase):
    """Dashboard overlay: /api/chart pc_ratio helper (ChartDataProcessor._pc_ratio_for)."""

    def test_pc_ratio_aligned_to_labels(self):
        import server
        path = _temp_db()
        with patch.object(db, "DB_PATH", path):
            try:
                db.upsert_reading("2026-08-04", "ticker", "AAPL", "put_call_oi_ratio",
                                  "oi_store", 0.55, 0.3, count=800)
                db.upsert_reading("2026-08-05", "ticker", "AAPL", "put_call_oi_ratio",
                                  "oi_store", 0.61, 0.2, count=900)
                db.upsert_reading("2026-08-05", "ticker", "MSFT", "put_call_oi_ratio",
                                  "oi_store", 1.2, -0.1, count=400)
                labels = ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06"]
                out = server.ChartDataProcessor._pc_ratio_for("AAPL", labels)
                # 08-03 has no reading -> None; readings carry FORWARD to later
                # labels (a Friday reading shows on Monday's bar too — the
                # on-demand weekend-snapshot case). 08-06 keeps 08-05's 0.61.
                self.assertEqual(out, [None, 0.55, 0.61, 0.61])
            finally:
                os.unlink(path)

    def test_pc_ratio_fail_open(self):
        import server
        path = _temp_db()
        with patch.object(db, "DB_PATH", path):
            try:
                # Empty store -> [] (fail-open, no overlay)
                self.assertEqual(server.ChartDataProcessor._pc_ratio_for("AAPL", ["2026-08-03"]), [])
                # Unrelated readings -> []
                db.upsert_reading("2026-08-04", "market", None, "vix", "cboe", 15.1, 0.8, count=1)
                self.assertEqual(server.ChartDataProcessor._pc_ratio_for("AAPL", ["2026-08-03"]), [])
            finally:
                os.unlink(path)


class TestCollectors(unittest.TestCase):
    """Phase 2 collectors: oi_store (own option_oi.db) + breadth (Yahoo ^NYAD)."""

    def setUp(self):
        import sentiment_collect
        self.collect = sentiment_collect
        self.db_path = _temp_db()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _make_oi_db(self, path, rows):
        """Build a throwaway option_oi.db with the same schema as the real one."""
        import sqlite3
        conn = sqlite3.connect(path)
        conn.executescript(
            "CREATE TABLE contract_oi (date TEXT, ticker TEXT, expiry TEXT, strike REAL, "
            "type TEXT, oi INTEGER, vol INTEGER, mid REAL, ts INTEGER, "
            "PRIMARY KEY (date, ticker, expiry, strike, type));"
        )
        conn.executemany(
            "INSERT INTO contract_oi VALUES (?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
        conn.close()

    def test_oi_ratio_collects_and_increments(self):
        oi_path = self.db_path + ".oi.db"
        self._make_oi_db(oi_path, [
            # date, ticker, expiry, strike, type, oi, vol, mid, ts
            ("2026-08-03", "AAPL", "2026-09-18", 200.0, "Call", 1000, 100, 5.0, 1),
            ("2026-08-03", "AAPL", "2026-09-18", 200.0, "Put", 500, 50, 4.0, 1),
            ("2026-08-04", "AAPL", "2026-09-18", 200.0, "Call", 1200, 120, 5.5, 2),
            ("2026-08-04", "AAPL", "2026-09-18", 200.0, "Put", 300, 30, 3.0, 2),
            # ticker with zero puts -> excluded (HAVING put_oi > 0)
            ("2026-08-04", "SPY", "2026-09-18", 500.0, "Call", 900, 90, 2.0, 2),
        ])
        with patch.object(self.collect, "_OI_DB", oi_path), \
             patch.object(db, "DB_PATH", self.db_path):
            n = self.collect.collect_oi_ratios()
            self.assertEqual(n, 2)  # AAPL 08-03 + 08-04; SPY excluded
            rows = db.query_readings(metric="put_call_oi_ratio")
            self.assertEqual(len(rows), 2)
            aapl_03 = [r for r in rows if r["asof_date"] == "2026-08-03"][0]
            self.assertAlmostEqual(aapl_03["value"], 0.5)   # 500/1000
            self.assertAlmostEqual(aapl_03["sentiment"], 2/1.5 - 1)  # call_share of 0.5 ratio
            # Incremental: re-run must not duplicate
            n2 = self.collect.collect_oi_ratios()
            self.assertEqual(n2, 0)
            self.assertEqual(len(db.query_readings(metric="put_call_oi_ratio")), 2)
        os.unlink(oi_path)

    def test_oi_ratio_missing_db_fail_open(self):
        with patch.object(self.collect, "_OI_DB", "/nonexistent/option_oi.db"):
            self.assertEqual(self.collect.collect_oi_ratios(), 0)

    def test_breadth_collects_one_row(self):
        import pandas as pd
        idx = pd.date_range("2026-08-01", periods=2, freq="D")
        # 4 up, 2 down -> share = (4-2)/6 = 1/3
        data = {"Close": pd.DataFrame({
            "AAPL": [100.0, 101.0], "MSFT": [100.0, 102.0], "NVDA": [100.0, 103.0],
            "GOOGL": [100.0, 104.0], "AMZN": [100.0, 99.0], "META": [100.0, 98.0],
        }, index=idx)}
        fake = pd.concat(data, axis=1)
        with patch.object(self.collect, "yf") as mock_yf:
            mock_yf.download.return_value = fake
            with patch.object(db, "DB_PATH", self.db_path):
                n = self.collect.collect_breadth()
                self.assertEqual(n, 1)
                rows = db.query_readings(metric="breadth_ad")
                self.assertEqual(len(rows), 1)
                self.assertAlmostEqual(rows[0]["value"], 1 / 3)
                self.assertAlmostEqual(rows[0]["sentiment"], 1 / 3)  # passthrough
                self.assertEqual(rows[0]["count"], 6)

    def test_breadth_fetch_fail_open(self):
        with patch.object(self.collect, "yf") as mock_yf:
            mock_yf.download.side_effect = Exception("network")
            with patch.object(db, "DB_PATH", self.db_path):
                self.assertEqual(self.collect.collect_breadth(), 0)

    def test_providers_registered(self):
        with patch.object(db, "DB_PATH", self.db_path):
            self.assertIn("oi_store", sentiment.PROVIDERS)
            self.assertIn("breadth", sentiment.PROVIDERS)
            provs = sentiment.list_providers()
            by_name = {p["name"]: p for p in provs}
            self.assertTrue(by_name["oi_store"]["configured"])
            self.assertTrue(by_name["breadth"]["configured"])

    # ---- Phase 3: CBOE + FINRA ----

    def test_vix_collects_latest_with_real_percentile(self):
        csv_text = "DATE,OPEN,HIGH,LOW,CLOSE\n"
        for i in range(1, 255):
            csv_text += f"01/{i%28+1:02d}/2026,10,10,10,{10+i/10:.1f}\n"  # rising closes
        csv_text += "08/07/2026,10,10,10,99.9\n"  # latest = max -> percentile_inv -> bearish
        with patch.object(self.collect, "_fetch_text", return_value=csv_text), \
             patch.object(db, "DB_PATH", self.db_path):
            n = self.collect.collect_cboe()
            self.assertEqual(n, 1)  # VIX row; P/C fetch returns None -> skipped
            rows = db.query_readings(metric="vix")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["asof_date"], "2026-08-07")
            self.assertAlmostEqual(rows[0]["value"], 99.9)
            self.assertLess(rows[0]["sentiment"], 0)  # high VIX = bearish

    def test_cboe_pc_parses_ratios(self):
        # Real page escaping: Next.js flight payload with \\\" (double-escaped quotes)
        page = (r'<script>self.__next_f.push([1,"24:[\\\"$\\\",{\\\"data\\\":{\\\"optionsData\\\":{\\\"ratios\\\":['
                r'{\\\"name\\\":\\\"TOTAL PUT/CALL RATIO\\\",\\\"value\\\":\\\"0.86\\\"},'
                r'{\\\"name\\\":\\\"INDEX PUT/CALL RATIO\\\",\\\"value\\\":\\\"1.03\\\"},'
                r'{\\\"name\\\":\\\"EQUITY PUT/CALL RATIO\\\",\\\"value\\\":\\\"0.57\\\"}]}}}"])</script>')
        with patch.object(self.collect, "_fetch_text", side_effect=[None, page]), \
             patch.object(db, "DB_PATH", self.db_path):
            n = self.collect.collect_cboe()
            self.assertEqual(n, 2)  # equity + index
            eq = db.query_readings(metric="cboe_pc_equity")
            idx = db.query_readings(metric="cboe_pc_index")
            self.assertEqual(len(eq), 1)
            self.assertAlmostEqual(eq[0]["value"], 0.57)
            self.assertAlmostEqual(idx[0]["value"], 1.03)
            self.assertIsNone(eq[0]["sentiment"])  # no strip history yet -> gray

    def test_finra_collects_universe_tickers(self):
        page = '<a href="https://cdn.finra.org/equity/otcmarket/biweekly/shrt20260615.csv">x</a>' \
               '<a href="https://cdn.finra.org/equity/otcmarket/biweekly/shrt20260715.csv">x</a>'
        header = "accountingYearMonthNumber|symbolCode|issueName|issuerServicesGroupExchangeCode|" \
                 "marketClassCode|currentShortPositionQuantity|previousShortPositionQuantity|" \
                 "stockSplitFlag|averageDailyVolumeQuantity|daysToCoverQuantity|revisionFlag|" \
                 "changePercent|changePreviousNumber|settlementDate\n"
        file_text = header
        file_text += "20260715|AAPL|Apple|A|NYSE|50000000|0||10000000|5.0||0|0|2026-07-15\n"
        file_text += "20260715|MSFT|Microsoft|A|NYSE|80000000|0||20000000|4.0||0|0|2026-07-15\n"
        file_text += "20260715|ZZHGF|Zhongan|S|OTC|100|0||0|999.99||0|0|2026-07-15\n"  # sentinel skipped
        with patch.object(self.collect, "_fetch_text", side_effect=[page, file_text]), \
             patch.object(db, "DB_PATH", self.db_path):
            n = self.collect.collect_finra_short_interest()
            self.assertEqual(n, 2)  # AAPL + MSFT; ZZHGF sentinel skipped
            rows = db.query_readings(metric="short_interest_dtc")
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["asof_date"], "2026-07-15")
            aapl = [r for r in rows if r["ticker"] == "AAPL"][0]
            self.assertAlmostEqual(aapl["value"], 5.0)
            self.assertIsNone(aapl["sentiment"])  # no history yet -> gray

    def test_finra_incremental_skips_stored_settlement(self):
        page = '<a href="https://cdn.finra.org/equity/otcmarket/biweekly/shrt20260715.csv">x</a>'
        file_text = "h|AAPL|Apple|A|NYSE|100|0||10|3.0||0|0|2026-07-15\n"
        with patch.object(self.collect, "_fetch_text", side_effect=[page, file_text]), \
             patch.object(db, "DB_PATH", self.db_path):
            self.assertEqual(self.collect.collect_finra_short_interest(), 1)
            # second run: same settlement -> 0 rows (incremental)
            with patch.object(self.collect, "_fetch_text", side_effect=[page, file_text]):
                self.assertEqual(self.collect.collect_finra_short_interest(), 0)
            self.assertEqual(len(db.query_readings(metric="short_interest_dtc")), 1)

    def test_cboe_fetch_fail_open(self):
        with patch.object(self.collect, "_fetch_text", return_value=None), \
             patch.object(db, "DB_PATH", self.db_path):
            self.assertEqual(self.collect.collect_cboe(), 0)
            self.assertEqual(self.collect.collect_finra_short_interest(), 0)

    # ---- Phase 4: NAAIM + FRED ----

    def test_naaim_parses_embeddable_chart(self):
        import html, json
        chart = {
            "type": "line",
            "data": {
                "labels": ["2026-07-22", "2026-07-29", "2026-08-05"],
                "datasets": [{"label": "NAAIM", "data": [84.0, 79.7, 91.2]}],
            },
        }
        page = ('<canvas data-controller="symfony--ux-chartjs--chart" '
                f'data-symfony--ux-chartjs--chart-view-value="{html.escape(json.dumps(chart))}"></canvas>')
        with patch.object(self.collect, "_fetch_text", return_value=page), \
             patch.object(db, "DB_PATH", self.db_path):
            n = self.collect.collect_naaim()
            self.assertEqual(n, 1)
            rows = db.query_readings(metric="naaim_exposure")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["asof_date"], "2026-08-05")
            self.assertAlmostEqual(rows[0]["value"], 91.2)
            # center_50: (91.2-50)/50 = +0.824
            self.assertAlmostEqual(rows[0]["sentiment"], 0.824)

    def test_naaim_fail_open(self):
        with patch.object(self.collect, "_fetch_text", return_value=None), \
             patch.object(db, "DB_PATH", self.db_path):
            self.assertEqual(self.collect.collect_naaim(), 0)

    def test_fred_removed(self):
        """UMich/Conf Board removed (macro data) — no FRED provider, no series."""
        self.assertFalse(hasattr(self.collect, "collect_fred"))
        self.assertFalse(hasattr(self.collect, "FRED_SERIES"))
        import sentiment
        self.assertNotIn("fred", sentiment.PROVIDERS)
        with patch.object(db, "DB_PATH", self.db_path):
            metrics = db.get_metrics()
            self.assertNotIn("umich", [m["metric"] for m in metrics])
            self.assertNotIn("conf_board", [m["metric"] for m in metrics])

    # ---- Phase 5: Alpha Vantage NEWS_SENTIMENT (market aggregate) ----

    def test_av_requires_key_fail_open(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ALPHA_VANTAGE_API_KEY", None)
            with patch.object(db, "DB_PATH", self.db_path):
                self.assertEqual(self.collect.collect_av_news(), 0)

    def test_av_aggregates_market_score(self):
        import requests
        fake = MagicMock(); fake.status_code = 200
        fake.json.return_value = {"items": "3", "feed": [
            {"overall_sentiment_score": "0.5"},
            {"overall_sentiment_score": "-0.1"},
            {"overall_sentiment_score": "0.3"},
        ]}
        with patch.dict(os.environ, {"ALPHA_VANTAGE_API_KEY": "k"}), \
             patch.object(db, "DB_PATH", self.db_path), \
             patch.object(requests, "get", return_value=fake) as mg:
            n = self.collect.collect_av_news()
            self.assertEqual(n, 1)
            # one no-ticker call (AV tickers filter is intersection-based: multi-ticker returns 0)
            args, kwargs = mg.call_args
            params = kwargs.get("params", {})
            self.assertEqual(params["function"], "NEWS_SENTIMENT")
            self.assertNotIn("tickers", params)
            rows = db.query_readings(metric="news_sentiment")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["scope"], "market")
            self.assertEqual(rows[0]["source"], "alphavantage")
            self.assertAlmostEqual(rows[0]["value"], (0.5 - 0.1 + 0.3) / 3)
            self.assertAlmostEqual(rows[0]["sentiment"], (0.5 - 0.1 + 0.3) / 3)  # passthrough
            self.assertEqual(rows[0]["count"], 3)

    def test_av_fail_open_on_bad_response(self):
        import requests
        fake = MagicMock(); fake.status_code = 200
        fake.json.return_value = {"Information": "premium endpoint requires key"}
        with patch.dict(os.environ, {"ALPHA_VANTAGE_API_KEY": "k"}), \
             patch.object(db, "DB_PATH", self.db_path), \
             patch.object(requests, "get", return_value=fake):
            self.assertEqual(self.collect.collect_av_news(), 0)
            self.assertEqual(db.query_readings(metric="news_sentiment"), [])

    def test_av_skips_unparseable_scores(self):
        import requests
        fake = MagicMock(); fake.status_code = 200
        fake.json.return_value = {"feed": [
            {"overall_sentiment_score": "0.4"},
            {"overall_sentiment_score": None},
            {"overall_sentiment_score": "junk"},
        ]}
        with patch.dict(os.environ, {"ALPHA_VANTAGE_API_KEY": "k"}), \
             patch.object(db, "DB_PATH", self.db_path), \
             patch.object(requests, "get", return_value=fake):
            self.assertEqual(self.collect.collect_av_news(), 1)
            rows = db.query_readings(metric="news_sentiment")
            self.assertAlmostEqual(rows[0]["value"], 0.4)
            self.assertEqual(rows[0]["count"], 1)

    # ---- AAII (Firecrawl scrape bypass of Imperva) ----

    def test_aaii_requires_key_fail_open(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FIRECRAWL_API_KEY", None)
            with patch.object(db, "DB_PATH", self.db_path):
                self.assertEqual(self.collect.collect_aaii(), 0)

    def test_aaii_parses_week_and_spread(self):
        import requests
        md = ("# AAII Investor Sentiment Survey\n\n"
              "Week Ending\nSentiment Votes\nBullish Neutral Bearish\n\n"
              "8/5/2026\n37.0%\n25.0%\n38.0%\n\n"
              "7/29/2026\n31.0%\n26.9%\n42.1%")
        fake = MagicMock(); fake.status_code = 200
        fake.json.return_value = {"data": {"markdown": md}}
        with patch.dict(os.environ, {"FIRECRAWL_API_KEY": "fc-test"}), \
             patch.object(db, "DB_PATH", self.db_path), \
             patch.object(requests, "post", return_value=fake) as mg:
            n = self.collect.collect_aaii()
            self.assertEqual(n, 1)
            # Firecrawl scrape called with the survey URL in the request body
            api_url = mg.call_args[0][0]
            self.assertEqual(api_url, self.collect.FIRECRAWL_API)
            self.assertEqual(mg.call_args.kwargs["json"]["url"], self.collect.AAII_SURVEY_URL)
            rows = db.query_readings(metric="aaii_bull_bear_spread")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["asof_date"], "2026-08-05")  # 8/5/2026 -> ISO
            self.assertAlmostEqual(rows[0]["value"], 37.0 - 38.0)   # bull - bear = -1.0
            self.assertAlmostEqual(rows[0]["sentiment"], -0.01)     # spread_100
            self.assertEqual(rows[0]["source"], "aaii")

    def test_aaii_fail_open_on_bad_markdown(self):
        import requests
        for bad in ["", "no table here", "1/1/2026\nnot percentages"]:
            fake = MagicMock(); fake.status_code = 200
            fake.json.return_value = {"data": {"markdown": bad}}
            with patch.dict(os.environ, {"FIRECRAWL_API_KEY": "fc-test"}), \
                 patch.object(db, "DB_PATH", self.db_path), \
                 patch.object(requests, "post", return_value=fake):
                self.assertEqual(self.collect.collect_aaii(), 0)

    # ---- COT (CFTC financial futures) + Margin Debt (FINRA) ----

    def test_cot_parses_financial_report(self):
        import io, zipfile, csv
        header = ["Market_and_Exchange_Names", "Report_Date_as_YYYY-MM-DD", "Open_Interest_All",
                  "Asset_Mgr_Positions_Long_All", "Asset_Mgr_Positions_Short_All",
                  "Lev_Money_Positions_Long_All", "Lev_Money_Positions_Short_All"]
        rows = [
            ["E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE", "2026-08-04", "2116079",
             "1162320", "225287", "206039", "536038"],
            ["E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE", "2026-07-28", "2000000",
             "1000000", "200000", "200000", "500000"],
            ["OTHER MARKET - CHICAGO MERCANTILE EXCHANGE", "2026-08-04", "100",
             "1", "1", "1", "1"],
        ]
        buf = io.StringIO()
        w = csv.writer(buf); w.writerow(header); w.writerows(rows)
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as z:
            z.writestr("FinFutYY.txt", buf.getvalue())
        zip_buf.seek(0)

        import requests
        fake = MagicMock(); fake.status_code = 200
        fake.content = zip_buf.getvalue()
        with patch.object(db, "DB_PATH", self.db_path), \
             patch.object(requests, "get", return_value=fake) as mg:
            n = self.collect.collect_cot()
            self.assertEqual(n, 1)
            self.assertIn("fut_fin_txt_", mg.call_args[0][0])  # financial futures file
            r = db.query_readings(metric="cot_net_spec")[0]
            self.assertEqual(r["asof_date"], "2026-08-04")
            # net spec = (1162320+206039) - (225287+536038) = 607034
            self.assertAlmostEqual(r["value"], 607034.0)
            self.assertEqual(r["count"], 2116079)  # open interest
            self.assertEqual(r["source"], "cot")

    def test_cot_fail_open(self):
        import requests
        for fake_content in [b"not a zip", b""]:
            fake = MagicMock(); fake.status_code = 200
            fake.content = fake_content
            with patch.object(db, "DB_PATH", self.db_path), \
                 patch.object(requests, "get", return_value=fake):
                self.assertEqual(self.collect.collect_cot(), 0)

    def test_margin_parses_table(self):
        page = ("<html><table>"
                "<tr><th>Month/Year</th><th>Debit Balances</th></tr>"
                "<tr><td>Jun-26</td><td>1,502,072</td></tr>"
                "<tr><td>May-26</td><td>1,415,557</td></tr>"
                "</table></html>")
        with patch.object(self.collect, "_fetch_text", return_value=page), \
             patch.object(db, "DB_PATH", self.db_path):
            n = self.collect.collect_margin_debt()
            self.assertEqual(n, 1)
            r = db.query_readings(metric="margin_debt")[0]
            self.assertEqual(r["asof_date"], "2026-06-01")  # Jun-26 -> 2026-06
            self.assertAlmostEqual(r["value"], 1502.072)  # $M/1000 -> $B
            self.assertEqual(r["source"], "finra_margin")
            self.assertIsNone(r["sentiment"])  # no history yet -> gray

    def test_margin_fail_open(self):
        with patch.object(self.collect, "_fetch_text", return_value=None), \
             patch.object(db, "DB_PATH", self.db_path):
            self.assertEqual(self.collect.collect_margin_debt(), 0)
        for bad in ["<html>no table</html>", "<table></table>", "<table><tr><td>x</td></tr></table>"]:
            with patch.object(self.collect, "_fetch_text", return_value=bad), \
                 patch.object(db, "DB_PATH", self.db_path):
                self.assertEqual(self.collect.collect_margin_debt(), 0)


if __name__ == "__main__":
    unittest.main()
