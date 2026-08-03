import unittest
from unittest.mock import patch, MagicMock
import pandas as pd
import sys
import os
import json
import math

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

    # ------------------------------------------------------------------
    # Liquidity flags + put-call parity (OMON improvements)
    # ------------------------------------------------------------------

    def test_mid_or_last_prefers_mid(self):
        row = {'bid': 4.9, 'ask': 5.1, 'last': 5.0}
        self.assertEqual(options._mid_or_last(row), 5.0)
        row = {'bid': 0, 'ask': 0, 'last': 5.0}
        self.assertEqual(options._mid_or_last(row), 5.0)
        row = {'bid': 0, 'ask': 0, 'last': 0}
        self.assertIsNone(options._mid_or_last(row))

    def test_check_put_call_parity_ok(self):
        # A strike whose implied forward equals the chain median -> OK.
        S, K, T, r = 100.0, 100.0, 0.25, 0.05
        fwd = 105.0  # median implied forward (whatever spot is)
        c_mid = 8.0
        p_mid = c_mid - (fwd - K * math.exp(-r * T))  # forces F == 105.0
        c = {'bid': c_mid - 0.1, 'ask': c_mid + 0.1, 'last': c_mid}
        p = {'bid': p_mid - 0.1, 'ask': p_mid + 0.1, 'last': p_mid}
        f = options.implied_forward(c, p, K, T, r)
        self.assertAlmostEqual(f, fwd, places=9)
        residual, ok = options.parity_residual(f, fwd, c, p)
        self.assertAlmostEqual(residual, 0.0, places=9)
        self.assertTrue(ok)

    def test_check_put_call_parity_ok_with_min_floor(self):
        # Small residual within the spot-based min_floor -> OK even though
        # the raw spread floor alone would flag it. (The ATM-noise case.)
        K, T, r = 100.0, 0.25, 0.05
        fwd_median = 105.0
        c = {'bid': 7.9, 'ask': 8.1, 'last': 8.0}   # mid 8.0
        p = {'bid': 2.26, 'ask': 2.46, 'last': 2.36}  # mid 2.36
        f = options.implied_forward(c, p, K, T, r)
        # fwd ~ 104.4 -> residual ~ -0.60; spread floor ~0.25
        residual, ok = options.parity_residual(f, fwd_median, c, p)
        self.assertFalse(ok)            # flagged without min_floor
        residual2, ok2 = options.parity_residual(f, fwd_median, c, p, min_floor=1.0)
        self.assertTrue(ok2)            # suppressed with min_floor

    def test_check_put_call_parity_violation(self):
        K, T, r = 100.0, 0.25, 0.05
        fwd_median = 105.0
        # Put is far too cheap -> implied forward blows past the median
        c = {'bid': 8.0, 'ask': 8.2, 'last': 8.1}
        p = {'bid': 0.5, 'ask': 0.7, 'last': 0.6}
        f = options.implied_forward(c, p, K, T, r)
        residual, ok = options.parity_residual(f, fwd_median, c, p, min_floor=1.0)
        self.assertIsNotNone(residual)
        self.assertFalse(ok)  # large residual flags even with min_floor

    def test_check_put_call_parity_no_price(self):
        K, T, r = 100.0, 0.25, 0.05
        f = options.implied_forward(
            {'bid': 0, 'ask': 0, 'last': 0},
            {'bid': 4.0, 'ask': 4.2, 'last': 4.1}, K, T, r)
        self.assertIsNone(f)

    @patch('yfinance.Ticker')
    @patch('options.calculate_greeks')
    def test_chain_has_liquidity_and_parity_fields(self, mock_greeks, mock_ticker):
        """The processed chain rows carry hasQuote/spread/illiquid + parity."""
        mock_instance = mock_ticker.return_value
        mock_instance.options = ['2023-12-15']
        mock_chain = MagicMock()
        mk = lambda last, bid, ask, strike: {
            'contractSymbol': f'X{strike}', 'strike': strike, 'lastPrice': last,
            'bid': bid, 'ask': ask, 'change': 0, 'percentChange': 0,
            'volume': 100, 'openInterest': 500, 'impliedVolatility': 0.25, 'itm': False}
        mock_chain.calls = pd.DataFrame([mk(6.1, 6.0, 6.2, 150.0)])
        mock_chain.puts = pd.DataFrame([mk(4.1, 4.0, 4.2, 150.0)])
        mock_instance.option_chain.return_value = mock_chain
        mock_instance.info = {'currentPrice': 150.0}
        mock_greeks.return_value = {'delta': 0.5, 'gamma': 0.02, 'theta': -0.1, 'vega': 0.1, 'rho': 0.01}

        result = options.get_options_chain('AAPL', '2023-12-15', use_cache=False)
        c = result['calls'][0]
        p = result['puts'][0]
        self.assertTrue(c['hasQuote'])
        self.assertEqual(c['spread'], 0.2)
        self.assertIsNotNone(c['spreadPct'])
        self.assertFalse(c['illiquid'])
        # both sides carry the same parity residual fields
        self.assertIn('parityResidual', c)
        self.assertIn('parityOk', c)
        self.assertEqual(c['parityResidual'], p['parityResidual'])

    @patch('yfinance.Ticker')
    @patch('options.calculate_greeks')
    def test_chain_flags_illiquid_row(self, mock_greeks, mock_ticker):
        mock_instance = mock_ticker.return_value
        mock_instance.options = ['2023-12-15']
        mock_chain = MagicMock()
        mk = lambda last, bid, ask, strike: {
            'contractSymbol': f'X{strike}', 'strike': strike, 'lastPrice': last,
            'bid': bid, 'ask': ask, 'change': 0, 'percentChange': 0,
            'volume': 0, 'openInterest': 0, 'impliedVolatility': None, 'itm': False}
        mock_chain.calls = pd.DataFrame([mk(0, 0, 0, 150.0)])
        mock_chain.puts = pd.DataFrame()
        mock_instance.option_chain.return_value = mock_chain
        mock_instance.info = {'currentPrice': 150.0}
        mock_greeks.return_value = {'delta': 0.5, 'gamma': 0.02, 'theta': -0.1, 'vega': 0.1, 'rho': 0.01}

        result = options.get_options_chain('AAPL', '2023-12-15', use_cache=False)
        c = result['calls'][0]
        self.assertFalse(c['hasQuote'])
        self.assertTrue(c['illiquid'])
        self.assertIsNone(c['spread'])
        self.assertIsNone(c['iv'])  # no price, no real IV

if __name__ == '__main__':
    unittest.main()
