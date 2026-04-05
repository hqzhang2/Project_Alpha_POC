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

    def test_api_quotes(self):
        with patch('quotes.get_quotes') as mock_get:
            mock_get.return_value = [{'ticker': 'AAPL', 'price': 150.0}]
            self.handler.path = '/api/quotes?tickers=AAPL'
            self.handler.do_GET()
            self.handler.send_json.assert_called()

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

    def test_api_not_found(self):
        self.handler.path = '/api/nonexistent'
        self.handler.do_GET()
        self.handler.send_error.assert_called_with(404, "API not found")

    def test_static_html(self):
        with patch('os.path.exists') as mock_exists, \
             patch('builtins.open', unittest.mock.mock_open(read_data='<html><div class="header"></div></html>')), \
             patch('server.os.path.exists') as mock_exists_server:
            mock_exists.side_effect = lambda x: True
            mock_exists_server.return_value = True
            self.handler.path = '/'
            self.handler.do_GET()
            self.handler.send_response.assert_called_with(200)

if __name__ == '__main__':
    unittest.main()
