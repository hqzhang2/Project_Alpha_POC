import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
import sys, os, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(".")))
import server

class TestChartDataLogic(unittest.TestCase):

    @patch("yfinance.Ticker")
    def test_get_chart_data_1d_partial_data_padded(self, mock_ticker):
        today = datetime.date.today()
        dates = pd.date_range(f"{today} 09:30", f"{today} 10:30", freq="1min", tz="America/New_York")
        df = pd.DataFrame({"Close": [100.0 + i for i in range(len(dates))], "Volume": [1000] * len(dates)}, index=dates)
        
        mock_instance = mock_ticker.return_value
        mock_instance.history.return_value = df
        mock_instance.info = {"previousClose": 99.0}
        
        result = server.ChartDataProcessor.get_1d_chart("AAPL")
        
        self.assertEqual(len(result["labels"]), 391)
        self.assertEqual(len(result["prices"]), 391)
        
        # Should have real data at start, None at end
        self.assertEqual(result["prices"][0], 100.0)
        self.assertIsNone(result["prices"][-1])  # Last should be padded
        
        # Should have at least some None values (padded)
        none_count = sum(1 for p in result["prices"] if p is None)
        self.assertGreater(none_count, 0, "Should have padded None values")

if __name__ == "__main__":
    unittest.main()
