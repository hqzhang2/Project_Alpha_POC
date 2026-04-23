import unittest
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import estimates
import sec_financials

class TestEEAndFallback(unittest.TestCase):
    def test_estimates(self):
        res = estimates.get_estimates("AAPL")
        self.assertIn("summary", res)
        self.assertEqual(res["ticker"], "AAPL")
        
    def test_sec_fallback(self):
        # BHP uses fallback to yahoo
        res = sec_financials.fetch_financials("BHP", 4, "Q")
        self.assertIn("income", res)

if __name__ == '__main__':
    unittest.main()
