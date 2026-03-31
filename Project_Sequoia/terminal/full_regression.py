import requests
import unittest
import pandas as pd
import datetime

class TerminalRegressionTest(unittest.TestCase):
    BASE_URL = "http://localhost:9098/api"
    TICKERS = ["SPY", "AAPL"]

    def test_dashboard_and_chart_api(self):
        """Regression for Dashboard / Chart API (Sprint 4)"""
        print("\nTesting Dashboard/Chart API...")
        for ticker in self.TICKERS:
            # Test 1D high-res chart
            resp = requests.get(f"{self.BASE_URL}/chart?ticker={ticker}&tf=1D")
            self.assertEqual(resp.status_code, 200, f"Failed chart fetch for {ticker}")
            data = resp.json()
            
            self.assertIn('labels', data)
            self.assertIn('prices', data)
            self.assertIn('last_close', data)
            
            # Check 09:30 - 16:00 skeleton (391 minutes)
            self.assertEqual(len(data['labels']), 391, f"Chart labels for {ticker} should be 391 mins")
            self.assertEqual(data['labels'][0], '09:30')
            self.assertEqual(data['labels'][-1], '16:00')
            print(f"✅ Dashboard logic passed for {ticker}")

    def test_omon_api(self):
        """Regression for OMON / Options API (Sprint 2)"""
        print("\nTesting OMON/Options API...")
        ticker = "SPY"
        # 1. Fetch expirations
        resp = requests.get(f"{self.BASE_URL}/expirations?ticker={ticker}")
        self.assertEqual(resp.status_code, 200)
        exp_data = resp.json()
        self.assertTrue(len(exp_data['expirations']) > 0)
        
        # 2. Fetch chain for first expiration
        expiry = exp_data['expirations'][0]
        resp = requests.get(f"{self.BASE_URL}/options?ticker={ticker}&expiry={expiry}")
        self.assertEqual(resp.status_code, 200)
        chain = resp.json()
        self.assertIn('calls', chain)
        self.assertIn('puts', chain)
        self.assertIn('spot', chain)
        print(f"✅ OMON logic passed for {ticker} (Expiry: {expiry})")

    def test_ratio_api(self):
        """Regression for Ratio Analysis API (Sprint 3)"""
        print("\nTesting Ratio Analysis API...")
        resp = requests.get(f"{self.BASE_URL}/ratio?t1=XLE&t2=SPY&tf=1Y&sma=20")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('ratio', data)
        self.assertIn('sma', data)
        self.assertIn('rsi', data)
        self.assertEqual(data['t1_name'], 'XLE')
        print("✅ Ratio Analysis logic passed")

    def test_financials_api(self):
        """Regression for Financial Analyzer API (SEC/Sprint 3)"""
        print("\nTesting Financial Analyzer API...")
        ticker = "MSFT"
        resp = requests.get(f"{self.BASE_URL}/sec/financials?ticker={ticker}&periods=4&type=Q")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        for key in ['income', 'balance', 'cashflow', 'metrics']:
            self.assertIn(key, data, f"Missing key {key} in financials")
        print(f"✅ Financials logic passed for {ticker}")

if __name__ == "__main__":
    unittest.main()
