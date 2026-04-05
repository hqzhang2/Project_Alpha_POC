import unittest
from unittest.mock import patch, MagicMock
import pandas as pd
import sys
import os
import datetime

# Add the terminal directory to the path so we can import server modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yahoo_financials

class TestYahooFinancials(unittest.TestCase):

    @patch('yfinance.Ticker')
    def test_get_financials_empty(self, mock_ticker):
        mock_instance = mock_ticker.return_value
        
        # Mock empty dataframes
        mock_instance.income_stmt = pd.DataFrame()
        mock_instance.quarterly_income_stmt = pd.DataFrame()
        mock_instance.balance_sheet = pd.DataFrame()
        mock_instance.quarterly_balance_sheet = pd.DataFrame()
        mock_instance.cashflow = pd.DataFrame()
        mock_instance.quarterly_cashflow = pd.DataFrame()

        result = yahoo_financials.get_financials('DUMMY')
        
        self.assertEqual(result['income'], [])
        self.assertEqual(result['balance'], [])
        self.assertEqual(result['cashflow'], [])

    @patch('yfinance.Ticker')
    def test_get_financials_with_data(self, mock_ticker):
        mock_instance = mock_ticker.return_value
        
        # Mock index
        dates = pd.DatetimeIndex(['2023-12-31', '2022-12-31'])
        
        # Mock income statement
        income_df = pd.DataFrame({
            'Total Revenue': [1000, 900],
            'Net Income': [100, 90]
        }, index=dates).T
        mock_instance.income_stmt = income_df
        
        # Mock balance sheet
        balance_df = pd.DataFrame({
            'Total Assets': [5000, 4000],
            'Total Liabilities': [2000, 1500]
        }, index=dates).T
        mock_instance.balance_sheet = balance_df
        
        # Mock cashflow
        cashflow_df = pd.DataFrame({
            'Operating Cash Flow': [200, 150],
            'Free Cash Flow': [100, 50]
        }, index=dates).T
        mock_instance.cashflow = cashflow_df

        result = yahoo_financials.get_financials('DUMMY', periods=2)
        
        # Assert income structure
        self.assertEqual(len(result['income']), 2)
        self.assertEqual(result['income'][0]['period'], '2023-12-31')
        self.assertEqual(result['income'][0]['revenue'], 1000)
        self.assertEqual(result['income'][0]['net_income'], 100)
        self.assertEqual(result['income'][0]['source'], 'yahoo')

        # Assert balance structure
        self.assertEqual(len(result['balance']), 2)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        self.assertEqual(result['balance'][0]['total_liabilities'], 2000)

        # Assert cashflow structure
        self.assertEqual(len(result['cashflow']), 2)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 200)
        self.assertEqual(result['cashflow'][0]['free_cf'], 100)

    @patch('yfinance.Ticker')
    def test_get_financials_exception(self, mock_ticker):
        mock_ticker.side_effect = Exception("API Error")
        
        result = yahoo_financials.get_financials('DUMMY')
        
        self.assertEqual(result['income'], [])
        self.assertEqual(result['balance'], [])
        self.assertEqual(result['cashflow'], [])

if __name__ == '__main__':
    unittest.main()
