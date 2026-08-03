import unittest
from unittest.mock import patch, MagicMock
import pandas as pd
import sys
import os
import datetime

# Add the terminal directory to the path so we can import server modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yahoo_financials
import fundamentals

class TestYahooFinancials(unittest.TestCase):

    @patch.object(yahoo_financials.yf, 'Ticker')
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

    @patch.object(yahoo_financials.yf, 'Ticker')
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

    @patch.object(yahoo_financials.yf, 'Ticker')
    def test_get_financials_exception(self, mock_ticker):
        mock_ticker.side_effect = Exception("API Error")
        
        result = yahoo_financials.get_financials('DUMMY')
        
        self.assertEqual(result['income'], [])
        self.assertEqual(result['balance'], [])
        self.assertEqual(result['cashflow'], [])


class TestYahooFinancialsPeriodType(unittest.TestCase):
    """New behavior: period_type respected, correct type labels, info dict."""

    def _mock_ticker(self, mock_ticker):
        inst = mock_ticker.return_value
        dates_q = pd.DatetimeIndex(['2026-06-30', '2026-03-31', '2025-12-31'])
        dates_fy = pd.DatetimeIndex(['2026-06-30', '2025-06-30'])
        inst.quarterly_income_stmt = pd.DataFrame(
            {'Total Revenue': [100, 90, 80]}, index=dates_q).T
        inst.income_stmt = pd.DataFrame(
            {'Total Revenue': [400, 350]}, index=dates_fy).T
        inst.quarterly_balance_sheet = pd.DataFrame(
            {'Total Assets': [500, 450]}, index=dates_q[:2]).T
        inst.balance_sheet = pd.DataFrame(
            {'Total Assets': [900, 800]}, index=dates_fy).T
        inst.quarterly_cashflow = pd.DataFrame(
            {'Operating Cash Flow': [50, 40]}, index=dates_q[:2]).T
        inst.cashflow = pd.DataFrame(
            {'Operating Cash Flow': [150, 130]}, index=dates_fy).T
        inst.info = {'shortName': 'DUMMY', 'currentPrice': 30.0,
                     'marketCap': 3000, 'sharesOutstanding': 100}
        return inst

    @patch.object(yahoo_financials.yf, 'Ticker')
    def test_default_Q_uses_quarterly_and_labels_Q(self, mock_ticker):
        self._mock_ticker(mock_ticker)
        r = yahoo_financials.get_financials('DUMMY', periods=2)
        self.assertEqual(r['income'][0]['type'], 'Q')
        self.assertEqual(r['income'][0]['revenue'], 100)
        self.assertEqual(len(r['income']), 2)
        self.assertEqual(r['balance'][0]['type'], 'Q')

    @patch.object(yahoo_financials.yf, 'Ticker')
    def test_FY_uses_annual_and_labels_FY(self, mock_ticker):
        self._mock_ticker(mock_ticker)
        r = yahoo_financials.get_financials('DUMMY', periods=2, period_type='FY')
        self.assertEqual(r['income'][0]['type'], 'FY')
        self.assertEqual(r['income'][0]['revenue'], 400)
        self.assertEqual(r['cashflow'][0]['type'], 'FY')

    @patch.object(yahoo_financials.yf, 'Ticker')
    def test_empty_quarterly_falls_back_to_annual(self, mock_ticker):
        inst = self._mock_ticker(mock_ticker)
        inst.quarterly_income_stmt = pd.DataFrame()
        inst.quarterly_balance_sheet = pd.DataFrame()
        inst.quarterly_cashflow = pd.DataFrame()
        r = yahoo_financials.get_financials('DUMMY', periods=2)
        # Fallback rows are labeled with the ACTUAL source (FY), not 'Q'
        self.assertEqual(r['income'][0]['type'], 'FY')
        self.assertEqual(r['income'][0]['revenue'], 400)

    @patch.object(yahoo_financials.yf, 'Ticker')
    def test_info_dict_populated(self, mock_ticker):
        self._mock_ticker(mock_ticker)
        r = yahoo_financials.get_financials('DUMMY')
        self.assertEqual(r['info']['price'], 30.0)
        self.assertEqual(r['info']['market_cap'], 3000)
        self.assertEqual(r['info']['shares_outstanding'], 100)

    @patch.object(yahoo_financials.yf, 'Ticker')
    def test_balance_canonical_keys(self, mock_ticker):
        inst = self._mock_ticker(mock_ticker)
        inst.quarterly_balance_sheet = pd.DataFrame({
            'Total Assets': [500], 'Current Assets': [200],
            'Total Liabilities': [300], 'Current Liabilities': [150],
            'Total Stockholder Equity': [200], 'Long Term Debt': [80],
            'Short Term Debt': [20], 'Cash And Cash Equivalents': [60],
            'Net Receivables': [40], 'Inventory': [30],
        }, index=pd.DatetimeIndex(['2026-06-30'])).T
        r = yahoo_financials.get_financials('DUMMY', periods=1)
        b = r['balance'][0]
        self.assertEqual(b['long_term_debt'], 80)
        self.assertEqual(b['short_term_debt'], 20)
        self.assertEqual(b['total_equity'], 200)
        self.assertEqual(b['net_receivables'], 40)

    @patch.object(yahoo_financials.yf, 'Ticker')
    def test_info_error_returns_empty_info(self, mock_ticker):
        inst = self._mock_ticker(mock_ticker)
        inst.info = None  # None.get raises -> _get_info catches -> {} 
        r = yahoo_financials.get_financials('DUMMY')
        self.assertIn('info', r)
        self.assertEqual(r['info'], {})
        self.assertEqual(r['income'][0]['type'], 'Q')


class TestYahooFinancialsCurrency(unittest.TestCase):
    """Foreign-issuer statements are normalized to USD."""

    def _mock_ticker(self, mock_ticker, fin_cur='CNY'):
        inst = mock_ticker.return_value
        dates_q = pd.DatetimeIndex(['2026-06-30', '2026-03-31'])
        inst.quarterly_income_stmt = pd.DataFrame(
            {'Total Revenue': [1000, 900], 'Net Income': [100, 90],
             'Diluted EPS': [10.0, 9.0]}, index=dates_q).T
        inst.quarterly_balance_sheet = pd.DataFrame(
            {'Total Assets': [5000], 'Current Assets': [2000],
             'Total Liabilities': [3000], 'Current Liabilities': [1000],
             'Common Stock Equity': [2000], 'Ordinary Shares Number': [500],
             'Cash And Cash Equivalents': [800]},
            index=dates_q[:1]).T
        inst.quarterly_cashflow = pd.DataFrame(
            {'Operating Cash Flow': [200]}, index=dates_q[:1]).T
        inst.income_stmt = pd.DataFrame()
        inst.balance_sheet = pd.DataFrame()
        inst.cashflow = pd.DataFrame()
        inst.info = {'shortName': 'DUMMY', 'currentPrice': 30.0,
                     'marketCap': 3000, 'sharesOutstanding': 500,
                     'financialCurrency': fin_cur}
        return inst

    @patch.object(fundamentals, 'usd_per_unit', return_value=0.1481)
    @patch.object(yahoo_financials.yf, 'Ticker')
    def test_home_currency_converted_to_usd(self, mock_ticker, mock_fx):
        self._mock_ticker(mock_ticker)
        r = yahoo_financials.get_financials('DUMMY', periods=2)
        self.assertAlmostEqual(r['income'][0]['revenue'], 148.1, places=6)
        self.assertAlmostEqual(r['income'][0]['eps_diluted'], 1.481, places=6)
        self.assertAlmostEqual(r['balance'][0]['total_assets'], 740.5, places=6)
        self.assertAlmostEqual(r['balance'][0]['cash'], 118.48, places=6)
        mock_fx.assert_called_once_with('CNY')

    @patch.object(fundamentals, 'usd_per_unit', return_value=1.0)
    @patch.object(yahoo_financials.yf, 'Ticker')
    def test_usd_reporter_no_conversion(self, mock_ticker, mock_fx):
        self._mock_ticker(mock_ticker, fin_cur='USD')
        r = yahoo_financials.get_financials('DUMMY', periods=2)
        self.assertEqual(r['income'][0]['revenue'], 1000)
        self.assertEqual(r['income'][0]['eps_diluted'], 10.0)

    @patch.object(fundamentals, 'usd_per_unit', return_value=0.1481)
    @patch.object(yahoo_financials.yf, 'Ticker')
    def test_common_stock_equity_alias_picked_up(self, mock_ticker, mock_fx):
        self._mock_ticker(mock_ticker)
        r = yahoo_financials.get_financials('DUMMY', periods=1)
        self.assertAlmostEqual(r['balance'][0]['total_equity'], 296.2, places=6)
        self.assertEqual(r['balance'][0]['shares_outstanding'], 500)

    @patch.object(fundamentals, 'usd_per_unit', return_value=0.1481)
    @patch.object(yahoo_financials.yf, 'Ticker')
    def test_cashflow_converted(self, mock_ticker, mock_fx):
        self._mock_ticker(mock_ticker)
        r = yahoo_financials.get_financials('DUMMY', periods=1)
        self.assertAlmostEqual(r['cashflow'][0]['operating_cf'], 29.62, places=6)

    @patch.object(fundamentals, 'usd_per_unit', return_value=0.1481)
    @patch.object(yahoo_financials.yf, 'Ticker')
    def test_missing_rows_become_none_not_nan(self, mock_ticker, mock_fx):
        # pandas .get() returns NaN for rows the statement doesn't have
        # (e.g. 'Cost of Revenue' on some ADR statements) — payload must
        # never carry bare NaN (breaks browser JSON.parse).
        import math
        self._mock_ticker(mock_ticker)
        r = yahoo_financials.get_financials('DUMMY', periods=1)
        for row in r['income'] + r['balance'] + r['cashflow']:
            for k, v in row.items():
                self.assertFalse(isinstance(v, float) and math.isnan(v),
                                 f'NaN leaked in {k}')

if __name__ == '__main__':
    unittest.main()
