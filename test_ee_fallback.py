import unittest
import sys
import os

sys.path.append(os.path.abspath('Project_Sequoia/terminal'))
import estimates
import sec_financials

class TestEEAndFallback(unittest.TestCase):
    def test_estimates(self):
        res = estimates.get_estimates("AAPL")
        self.assertIn("summary", res)
        self.assertEqual(res["ticker"], "AAPL")
        
if __name__ == '__main__':
    unittest.main()
