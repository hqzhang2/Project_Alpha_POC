import unittest
import pandas as pd
import numpy as np
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import indicators

class TestIndicators(unittest.TestCase):
    def setUp(self):
        # Create a simple upward trending series
        self.series = pd.Series([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20])

    def test_calculate_rsi(self):
        rsi = indicators.calculate_rsi(self.series, period=5)
        self.assertEqual(len(rsi), len(self.series))
        # For a constantly increasing series, RSI should be high
        self.assertGreater(rsi.iloc[-1], 50)

    def test_calculate_macd(self):
        macd, signal, hist = indicators.calculate_macd(self.series, fast=3, slow=6, signal=3)
        self.assertEqual(len(macd), len(self.series))
        self.assertEqual(len(signal), len(self.series))
        self.assertEqual(len(hist), len(self.series))

    def test_calculate_bollinger_bands(self):
        upper, lower = indicators.calculate_bollinger_bands(self.series, window=5)
        self.assertEqual(len(upper), len(self.series))
        self.assertEqual(len(lower), len(self.series))
        # Upper band should be greater than lower band
        self.assertTrue((upper.dropna() > lower.dropna()).all())

if __name__ == '__main__':
    unittest.main()
