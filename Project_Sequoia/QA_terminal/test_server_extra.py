import unittest
from unittest.mock import patch, MagicMock
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server

class TestServerExtra(unittest.TestCase):

    def setUp(self):
        # Silence log_message
        server.Handler.log_message = MagicMock()
        self.handler = server.Handler.__new__(server.Handler)
        self.handler.send_response = MagicMock()
        self.handler.send_header = MagicMock()
        self.handler.end_headers = MagicMock()
        self.handler.send_json = MagicMock()
        self.handler.send_error = MagicMock()
        from io import BytesIO
        self.handler.wfile = BytesIO()
        self.handler.headers = {}
        server._quote_cache.clear()
        server._oi_results.clear()
        server._chart_cache_1d.clear()
        server._chart_cache_hist.clear()
        server._chart_1d_last.clear()

    def test_api_quotes(self):
        with patch('quotes.get_quotes') as mock_get:
            mock_get.return_value = [{'ticker': 'AAPL', 'price': 150.0}]
            self.handler.path = '/api/quotes?tickers=AAPL'
            self.handler.do_GET()
            self.handler.send_json.assert_called()

    def test_api_quotes_served_from_cache(self):
        """Second request within TTL must not call the provider again."""
        with patch('quotes.get_quotes') as mock_get:
            mock_get.return_value = {'ZZTEST': {'ticker': 'ZZTEST', 'price': 1.0}}
            self.handler.path = '/api/quotes?tickers=ZZTEST'
            self.handler.do_GET()
            self.handler.do_GET()
            self.assertEqual(mock_get.call_count, 1)

    def test_api_quotes_cap_and_empty(self):
        self.handler.path = '/api/quotes?tickers=' + ','.join('T%d' % i for i in range(60))
        self.handler.do_GET()
        self.handler.send_json.assert_called_with({'error': 'too many tickers (max 50)'}, status=400)
        self.handler.path = '/api/quotes?tickers='
        self.handler.do_GET()
        self.handler.send_json.assert_called_with({'error': 'tickers required'}, status=400)

    def test_api_quotes_dedupes_and_uppercases(self):
        with patch('quotes.get_quotes') as mock_get:
            mock_get.return_value = {'AAPL': {'ticker': 'AAPL'}}
            self.handler.path = '/api/quotes?tickers=aapl,AAPL'
            self.handler.do_GET()
            mock_get.assert_called_once_with(['AAPL'])

    def test_api_quotes_sends_timestamp_header(self):
        """The /api/quotes response carries X-Quotes-Ts (cache set time) so the
        UI can show 'as of' and flag staleness."""
        with patch('quotes.get_quotes') as mock_get:
            mock_get.return_value = {'ZZTS': {'ticker': 'ZZTS', 'price': 1.0}}
            self.handler.path = '/api/quotes?tickers=ZZTS'
            self.handler.do_GET()
            call = self.handler.send_json.call_args
            self.assertIn('X-Quotes-Ts', call.kwargs['headers'])
            self.assertIn('X-Cache', call.kwargs['headers'])

    def test_api_options(self):
        with patch('options.get_options_chain') as mock_chain:
            mock_chain.return_value = {'calls': [], 'puts': []}
            self.handler.path = '/api/options?ticker=AAPL'
            self.handler.do_GET()
            self.handler.send_json.assert_called()

    def test_api_screen(self):
        with patch('options.get_expirations') as mock_exp, \
             patch('options.get_options_chain') as mock_chain:
            mock_exp.return_value = ['2023-12-15']
            mock_chain.return_value = {'calls': [], 'puts': []}
            self.handler.path = '/api/screen?ticker=AAPL'
            self.handler.do_GET()
            self.handler.send_json.assert_called()

    def test_api_expirations(self):
        with patch('options.get_expirations') as mock_exp:
            mock_exp.return_value = ['2023-12-15']
            self.handler.path = '/api/expirations?ticker=AAPL'
            self.handler.do_GET()
            self.handler.send_json.assert_called()

    def test_api_chart_1d(self):
        with patch('server.ChartDataProcessor.get_1d_chart') as mock_chart:
            mock_chart.return_value = {'labels': [], 'prices': []}
            self.handler.path = '/api/chart?ticker=AAPL&tf=1D'
            self.handler.do_GET()
            self.handler.send_json.assert_called()

    def test_api_chart_hist(self):
        with patch('server.ChartDataProcessor.get_historical_chart') as mock_chart:
            mock_chart.return_value = {'labels': [], 'prices': []}
            self.handler.path = '/api/chart?ticker=AAPL&tf=1M'
            self.handler.do_GET()
            self.handler.send_json.assert_called()

    def test_api_chart_hist_cached(self):
        """Second chart request within TTL must not hit the provider again."""
        with patch('server.ChartDataProcessor.get_historical_chart') as mock_chart:
            mock_chart.return_value = {'labels': ['2026-01-01'], 'prices': [100.0], 'volumes': [1]}
            self.handler.path = '/api/chart?ticker=ZZCHRT&tf=1Y'
            self.handler.do_GET()
            self.handler.do_GET()
            self.assertEqual(mock_chart.call_count, 1)

    def test_api_chart_1d_empty_falls_back_to_last_session(self):
        """Empty 1D result (weekend/closed) serves the last real session."""
        with patch('server.ChartDataProcessor.get_1d_chart') as mock_chart:
            mock_chart.return_value = {'labels': ['09:30'], 'prices': [None] * 391, 'volumes': []}
            server._chart_1d_last.set('ZZWKND', {'labels': ['09:30'], 'prices': [100.0], 'volumes': []})
            self.handler.path = '/api/chart?ticker=ZZWKND&tf=1D'
            self.handler.do_GET()
            self.handler.send_json.assert_called_with(
                {'labels': ['09:30'], 'prices': [100.0], 'volumes': []})

    def test_get_1d_chart_falls_back_to_last_session_in_window(self):
        """When today has no bars (weekend/holiday), get_1d_chart serves the
        most recent session from the 5d window instead of a blank chart."""
        import pandas as pd
        with patch('server.yf.Ticker') as mock_ticker:
            idx = pd.date_range('2026-08-07 09:30', periods=10, freq='5min')  # Friday only
            df = pd.DataFrame({'Close': [100.0] * 10, 'Volume': [1] * 10}, index=idx)
            mock_ticker.return_value.history.return_value = df
            mock_ticker.return_value.info = {'previousClose': 100.0}
            data = server.ChartDataProcessor.get_1d_chart('ZZFRIDAY')
            filled = [p for p in (data.get('prices') or []) if p is not None]
            self.assertGreater(len(filled), 0)

    def test_api_sec_watchlist(self):
        with patch('sec_financials.get_watchlist') as mock_get:
            mock_get.return_value = []
            self.handler.path = '/api/sec/financials?action=watchlist'
            self.handler.do_GET()
            self.handler.send_json.assert_called()

    def test_api_sec_add(self):
        with patch('sec_financials.add_to_watchlist') as mock_add:
            self.handler.path = '/api/sec/financials?action=add&ticker=AAPL'
            self.handler.do_GET()
            self.handler.send_json.assert_called()

    def test_api_ratio_real(self):
        # Trigger the actual logic in get_ratio_data
        import pandas as pd
        import numpy as np
        import yfinance as yf
        
        with patch('yfinance.Ticker') as mock_ticker, \
             patch('server.calculate_rsi') as mock_rsi, \
             patch('server.calculate_macd') as mock_macd, \
             patch('server.calculate_bollinger_bands') as mock_bb:
            
            mock_hist = MagicMock()
            # Return a Series with a DatetimeIndex
            dates = pd.date_range('2023-01-01', periods=100)
            mock_series = pd.Series(np.random.randn(100), index=dates)
            mock_ticker.return_value.history.return_value = pd.DataFrame({'Close': mock_series})
            
            mock_rsi.return_value = [0]*100
            mock_macd.return_value = ([0]*100, [0]*100, [0]*100)
            mock_bb.return_value = ([0]*100, [0]*100)
            
            self.handler.path = '/api/ratio?t1=AAPL&t2=SPY&tf=1Y'
            self.handler.do_GET()
            self.handler.send_json.assert_called()

    def test_api_sec_remove(self):
        with patch('sec_financials.remove_from_watchlist') as mock_remove:
            self.handler.path = '/api/sec/financials?action=remove&ticker=AAPL'
            self.handler.do_GET()
            self.handler.send_json.assert_called_with({'status': 'removed'})

    def test_api_sec_fetch(self):
        with patch('sec_financials.fetch_financials') as mock_fetch:
            mock_fetch.return_value = {}
            self.handler.path = '/api/sec/financials?ticker=AAPL'
            self.handler.do_GET()
            self.handler.send_json.assert_called()

    def test_api_oi_snapshot_enqueues(self):
        """Watchlist-add OI snapshot must return immediately and enqueue —
        never snapshot chains synchronously on the request thread."""
        with patch('server._oi_queue') as mock_q:
            mock_q.put = MagicMock()
            self.handler.path = '/api/oi/snapshot?ticker=AAPL'
            self.handler.do_GET()
            self.handler.send_json.assert_called_with({'ticker': 'AAPL', 'status': 'started'})
            mock_q.put.assert_called_once_with('AAPL')

    def test_api_oi_snapshot_status(self):
        server._oi_results['AAPL'] = {'status': 'done', 'reading': 1.25}
        self.handler.path = '/api/oi/snapshot?action=status&ticker=AAPL'
        self.handler.do_GET()
        self.handler.send_json.assert_called_with(
            {'ticker': 'AAPL', 'status': 'done', 'reading': 1.25})

    def test_api_not_found(self):
        self.handler.path = '/api/nonexistent'
        self.handler.do_GET()
        self.handler.send_error.assert_called_with(404, "API not found")

    def test_cors_allowlist(self):
        """CORS must only be served to localhost origins (portal iframe); a
        remote origin must not be able to read localhost APIs."""
        self.assertTrue(server._cors_allowed('http://localhost:8000'))
        self.assertTrue(server._cors_allowed('http://127.0.0.1:8000'))
        self.assertTrue(server._cors_allowed('http://localhost:9999'))
        self.assertFalse(server._cors_allowed('http://evil.example.com'))
        self.assertFalse(server._cors_allowed('https://localhost:8000'))
        self.assertFalse(server._cors_allowed(None))
        self.assertFalse(server._cors_allowed(''))

    def test_static_html(self):
        with patch('os.path.exists') as mock_exists, \
             patch('builtins.open', unittest.mock.mock_open(read_data='<html><div class="header"></div></html>')), \
             patch('server.os.path.exists') as mock_exists_server:
            mock_exists.side_effect = lambda x: True
            mock_exists_server.return_value = True
            self.handler.path = '/'
            self.handler.do_GET()
            self.handler.send_response.assert_called_with(200)

    def test_no_path_traversal(self):
        """Unnormalized /../ requests must 404 without reaching serve_file."""
        self.handler.path = '/../terminal/dashboard.html'
        with patch.object(server.Handler, 'serve_file',
                          side_effect=AssertionError('serve_file must not be called')) as sf:
            self.handler.do_GET()
        self.handler.send_error.assert_called_with(404, "Not found")
        sf.assert_not_called()

if __name__ == '__main__':
    unittest.main()
