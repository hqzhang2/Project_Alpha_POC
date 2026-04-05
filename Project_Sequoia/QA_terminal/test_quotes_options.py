import unittest
from unittest.mock import patch, MagicMock
import pandas as pd
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quotes
import options

class TestQuotesOptions(unittest.TestCase):

    @patch('yfinance.Ticker')
    def test_get_quotes(self, mock_ticker):
        mock_instance = mock_ticker.return_value
        mock_instance.info = {
            'shortName': 'Apple Inc',
            'currentPrice': 150.0,
            'regularMarketChange': 1.5,
            'regularMarketChangePercent': 1.0
        }
        mock_instance.history.return_value = pd.DataFrame()
        
        result = quotes.get_quotes(['AAPL'], use_cache=False)
        self.assertIn('AAPL', result)
        self.assertEqual(result['AAPL']['price'], 150.0)

    @patch('yfinance.Ticker')
    def test_get_expirations(self, mock_ticker):
        mock_ticker.return_value.options = ('2023-12-15', '2023-12-22')
        result = options.get_expirations('AAPL')
        self.assertEqual(result, ['2023-12-15', '2023-12-22'])

    @patch('yfinance.Ticker')
    @patch('options.calculate_greeks')
    def test_get_options_chain(self, mock_greeks, mock_ticker):
        mock_instance = mock_ticker.return_value
        mock_instance.options = ['2023-12-15']
        mock_chain = MagicMock()
        mock_chain.calls = pd.DataFrame({
            'contractSymbol': ['AAPL231215C00150000'],
            'strike': [150.0],
            'lastPrice': [5.0],
            'bid': [4.9],
            'ask': [5.1],
            'change': [0.1],
            'percentChange': [2.0],
            'volume': [100],
            'openInterest': [500],
            'impliedVolatility': [0.25],
            'itm': [False]
        })
        mock_chain.puts = pd.DataFrame()
        mock_instance.option_chain.return_value = mock_chain
        mock_instance.info = {'currentPrice': 150.0}
        mock_greeks.return_value = {'delta': 0.5, 'gamma': 0.02, 'theta': -0.1, 'vega': 0.1, 'rho': 0.01}
        
        result = options.get_options_chain('AAPL', '2023-12-15', use_cache=False)
        self.assertEqual(result['ticker'], 'AAPL')
        self.assertTrue(len(result['calls']) > 0)

    def test_safe_float(self):
        self.assertEqual(quotes.safe_float(10.5), 10.5)
        self.assertIsNone(quotes.safe_float("invalid"))

    def test_safe_ret(self):
        # Use assertAlmostEqual for floating point precision
        self.assertAlmostEqual(quotes.safe_ret(110, 100), 10.0)
        self.assertIsNone(quotes.safe_ret(110, 0))

    def test_get_options_json(self):
        with patch('options.get_options_chain') as mock_chain:
            mock_chain.return_value = {'ticker': 'AAPL'}
            result = options.get_options_json('AAPL')
            self.assertEqual(result, '{"ticker": "AAPL"}')

if __name__ == '__main__':
    unittest.main()
