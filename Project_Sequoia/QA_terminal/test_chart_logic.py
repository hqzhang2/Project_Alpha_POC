import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
import sys
import os
import datetime

# Add the terminal directory to the path so we can import server
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server

class TestChartDataLogic(unittest.TestCase):

    @patch('yfinance.Ticker')
    def test_get_chart_data_1d_empty(self, mock_ticker):
        mock_instance = mock_ticker.return_value
        mock_instance.history.return_value = pd.DataFrame()
        result = server.ChartDataProcessor.get_1d_chart('AAPL')
        self.assertTrue('error' in result or len(result['labels']) == 0)

    @patch('yfinance.Ticker')
    def test_get_chart_data_1d_with_full_data(self, mock_ticker):
        """Test with full 391 minutes of data (09:30-16:00)"""
        today = datetime.date.today()
        dates = pd.date_range(f"{today} 09:30", f"{today} 16:00", freq="1min", tz='America/New_York')
        df = pd.DataFrame({
            'Close': [100.0 + i for i in range(len(dates))],
            'Volume': [1000] * len(dates)
        }, index=dates)
        
        mock_instance = mock_ticker.return_value
        mock_instance.history.return_value = df
        mock_instance.info = {'previousClose': 99.0}
        
        result = server.ChartDataProcessor.get_1d_chart('AAPL')
        
        # Should have exactly 391 labels (09:30 to 16:00 inclusive)
        self.assertEqual(len(result['labels']), 391)
        self.assertEqual(result['labels'][0], '09:30')
        self.assertEqual(result['labels'][-1], '16:00')
        self.assertAlmostEqual(result['prices'][0], 100.0)
        self.assertEqual(result['prev_close'], 99.0)

    @patch('yfinance.Ticker')
    def test_get_chart_data_1d_partial_data_padded(self, mock_ticker):
        """Test that partial data is padded with nulls to fill the full day"""
        today = datetime.date.today()
        # Only 60 minutes of data (09:30-10:29)
        dates = pd.date_range(f"{today} 09:30", f"{today} 10:29", freq="1min", tz='America/New_York')
        df = pd.DataFrame({
            'Close': [100.0 + i for i in range(len(dates))],
            'Volume': [1000] * len(dates)
        }, index=dates)
        
        mock_instance = mock_ticker.return_value
        mock_instance.history.return_value = df
        mock_instance.info = {'previousClose': 99.0}
        
        result = server.ChartDataProcessor.get_1d_chart('AAPL')
        
        # Should still have 391 labels
        self.assertEqual(len(result['labels']), 391)
        self.assertEqual(result['labels'][0], '09:30')
        self.assertEqual(result['labels'][-1], '16:00')
        
        # First 60 prices should be real, rest should be None (padded)
        self.assertEqual(result['prices'][0], 100.0)
        self.assertEqual(result['prices'][59], 159.0)  # 60th price
        self.assertIsNone(result['prices'][60])  # 61st should be padded
        self.assertIsNone(result['prices'][-1])  # Last should be padded

    @patch('yfinance.Ticker')
    def test_get_chart_data_1d_no_data_returns_error(self, mock_ticker):
        """Test that empty data returns error"""
        mock_instance = mock_ticker.return_value
        mock_instance.history.return_value = pd.DataFrame()
        result = server.ChartDataProcessor.get_1d_chart('INVALID')
        self.assertIn('error', result)

    @patch('yfinance.Ticker')
    def test_get_chart_data_1d_labels_are_sequential(self, mock_ticker):
        """Test that all time labels are sequential from 09:30 to 16:00"""
        today = datetime.date.today()
        dates = pd.date_range(f"{today} 09:35", f"{today} 10:05", freq="1min", tz='America/New_York')
        df = pd.DataFrame({
            'Close': [100.0 + i for i in range(len(dates))],
            'Volume': [1000] * len(dates)
        }, index=dates)
        
        mock_instance = mock_ticker.return_value
        mock_instance.history.return_value = df
        mock_instance.info = {'previousClose': 99.0}
        
        result = server.ChartDataProcessor.get_1d_chart('AAPL')
        
        # Check first few labels
        expected_first = ['09:30', '09:31', '09:32', '09:33', '09:34', '09:35']
        for i, expected in enumerate(expected_first):
            self.assertEqual(result['labels'][i], expected)
        
        # Check last few labels
        expected_last = ['15:55', '15:56', '15:57', '15:58', '15:59', '16:00']
        for i, expected in enumerate(expected_last):
            self.assertEqual(result['labels'][-6+i], expected)


class TestIndicatorsImport(unittest.TestCase):
    """Test that indicators module imports correctly"""

    def test_indicators_functions_return_valid_data(self):
        """Test that real indicator functions work (not dummy zeros)"""
        import numpy as np
        # Create test data
        test_series = pd.Series([100, 102, 101, 103, 105, 104, 106, 108, 107, 109, 
                                  110, 111, 113, 112, 114, 116, 115, 117, 119, 120])
        
        # These should NOT return all zeros
        rsi = server.calculate_rsi(test_series)
        macd, signal, hist = server.calculate_macd(test_series)
        upper, lower = server.calculate_bollinger_bands(test_series)
        
        # RSI should have some non-zero values after initial warmup
        rsi_clean = [x for x in rsi if x != 0]
        self.assertGreater(len(rsi_clean), 0, "RSI should return non-zero values")
        
        # MACD should have some variation
        macd_clean = [x for x in macd if x != 0]
        self.assertGreater(len(macd_clean), 0, "MACD should return non-zero values")


if __name__ == '__main__':
    unittest.main()
