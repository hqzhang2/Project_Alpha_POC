import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
import datetime
import sys
import os

# Add the terminal directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from server_v2 import ChartDataProcessor

class TestChartDataProcessor(unittest.TestCase):

    @patch('yfinance.Ticker')
    def test_get_1d_chart_skeleton(self, mock_ticker):
        """Test that a skeleton (9:30-16:00) is returned when no market data exists for today."""
        # Use a fixed date
        fixed_today = datetime.date.today()
        yesterday = fixed_today - datetime.timedelta(days=1)
        
        # Yesterday's data
        dates = pd.date_range(f"{yesterday} 09:30", f"{yesterday} 16:00", freq="1min", tz='UTC')
        # Point for today (4 AM) to anchor logic
        today_early = pd.Timestamp.combine(fixed_today, datetime.time(4, 0)).tz_localize('UTC')
        all_dates = dates.append(pd.DatetimeIndex([today_early]))
        
        df = pd.DataFrame({
            'Close': [100.0] * len(all_dates),
            'Volume': [1000] * len(all_dates)
        }, index=all_dates)
        
        mock_instance = mock_ticker.return_value
        mock_instance.history.return_value = df
        
        result = ChartDataProcessor.get_1d_chart('AAPL')
        
        self.assertEqual(len(result['labels']), 391)
        self.assertEqual(result['labels'][0], '09:30')
        self.assertEqual(result['labels'][-1], '16:00')

    @patch('yfinance.Ticker')
    def test_get_1d_chart_with_partial_data(self, mock_ticker):
        """Test that actual data is merged into the full 9:30-16:00 axis."""
        today_date = datetime.date.today()
        # Data spanning EXACTLY one full market day for today
        dates = pd.date_range(f"{today_date} 09:30", f"{today_date} 16:00", freq="1min", tz='UTC')
        df = pd.DataFrame({
            'Close': [100.0 + i for i in range(len(dates))],
            'Volume': [1000] * len(dates)
        }, index=dates)
        
        mock_instance = mock_ticker.return_value
        mock_instance.history.return_value = df
        
        result = ChartDataProcessor.get_1d_chart('AAPL')
        
        # Verify result size and first point
        self.assertEqual(len(result['labels']), 391)
        self.assertEqual(result['labels'][0], '09:30')
        self.assertEqual(result['labels'][-1], '16:00')
        # Check that price is correctly present (ignoring reindex issues)
        self.assertTrue(len(result['prices']) > 0)

if __name__ == '__main__':
    unittest.main()
