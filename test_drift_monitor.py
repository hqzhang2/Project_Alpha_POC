import unittest
import pandas as pd
import numpy as np
import sqlite3
from unittest.mock import patch, MagicMock
from drift_monitor import (
    get_returns_from_db,
    calculate_rolling_correlation_matrix,
    detect_convergence,
    save_snapshot,
    main, # Import the new main function
    DATABASE_PATH,
    TICKERS,
    LOOKBACK_DAYS,
    ROLLING_CORRELATION_DAYS,
    DEFENSIVE_ASSETS,
    MARKET_ASSET,
    CONVERGENCE_THRESHOLD,
    OUTPUT_FILE
)

class TestDriftMonitor(unittest.TestCase):

    def setUp(self):
        # Setup for mocking, if needed for multiple tests
        pass

    @patch('sqlite3.connect')
    @patch('pandas.read_sql_query')
    def test_get_returns_from_db_success(self, mock_read_sql_query, mock_sqlite_connect):
        # Mocking a successful database query
        mock_df_data = {
            'date': pd.to_datetime(['2026-01-01', '2026-01-01', '2026-01-02', '2026-01-02']),
            'ticker': ['XLE', 'SPY', 'XLE', 'SPY'],
            'adj_close': [100, 200, 101, 202]
        }
        mock_read_sql_query.return_value = pd.DataFrame(mock_df_data)

        returns_df = get_returns_from_db(DATABASE_PATH, ['XLE', 'SPY'], 2)
        self.assertFalse(returns_df.empty)
        self.assertIn('XLE', returns_df.columns)
        self.assertIn('SPY', returns_df.columns)

        # Check if pct_change and fillna(0) worked
        expected_returns = pd.DataFrame({
            'XLE': [0.0, 0.01],
            'SPY': [0.0, 0.01]
        }, index=pd.to_datetime(['2026-01-01', '2026-01-02']))
        expected_returns.index.name = 'date' # Set the index name to match
        expected_returns.columns.name = 'ticker' # Set the columns name to match

        # Sort columns to ensure consistent order before comparison
        pd.testing.assert_frame_equal(returns_df.sort_index(axis=1), expected_returns.sort_index(axis=1))


    @patch('sqlite3.connect')
    @patch('pandas.read_sql_query')
    def test_get_returns_from_db_empty(self, mock_read_sql_query, mock_sqlite_connect):
        # Mocking an empty result from the database
        mock_read_sql_query.return_value = pd.DataFrame()
        returns_df = get_returns_from_db(DATABASE_PATH, ['XLE'], 2)
        self.assertTrue(returns_df.empty)

    @patch('sqlite3.connect')
    @patch('pandas.read_sql_query', side_effect=sqlite3.DatabaseError("Test DB Error"))
    def test_get_returns_from_db_error(self, mock_read_sql_query, mock_sqlite_connect):
        returns_df = get_returns_from_db(DATABASE_PATH, ['XLE'], 2)
        self.assertTrue(returns_df.empty)

    def test_calculate_rolling_correlation_matrix_success(self):
        # Sample returns data
        returns_data = pd.DataFrame({
            'XLE': np.random.rand(100),
            'XLU': np.random.rand(100),
            'SPY': np.random.rand(100)
        })
        returns_data.index = pd.to_datetime(pd.date_range(start='2026-01-01', periods=100))
        returns_data.index.name = 'date' # Ensure index has a name
        rolling_corr = calculate_rolling_correlation_matrix(returns_data, 20)
        self.assertIsNotNone(rolling_corr)
        self.assertFalse(rolling_corr.empty)
        # Check if the output has a multi-index (date, ticker)
        self.assertTrue(isinstance(rolling_corr.index, pd.MultiIndex))
        self.assertIn('XLE', rolling_corr.columns)

    def test_calculate_rolling_correlation_matrix_empty(self):
        returns_data = pd.DataFrame()
        rolling_corr = calculate_rolling_correlation_matrix(returns_data, 20)
        self.assertIsNone(rolling_corr)

    def test_detect_convergence_alert_triggered(self):
        # Create a mock rolling correlation matrix where defensive assets are highly correlated with market
        dates = pd.to_datetime(pd.date_range(start='2026-01-01', periods=2))
        index = pd.MultiIndex.from_product([dates, ['XLU', 'SPY', 'GLD']], names=['date', 'ticker'])

        # Manually create a structure that mimics rolling().corr() output
        data_for_rolling = []
        for d in dates:
            # Define correlation matrices for each date
            if d == dates[0]:
                corr_matrix_daily = pd.DataFrame({
                    'XLU': [1.0, 0.8, 0.8], 'SPY': [0.8, 1.0, 0.7], 'GLD': [0.8, 0.7, 1.0]
                }, index=['XLU', 'SPY', 'GLD'], columns=['XLU', 'SPY', 'GLD'])
            else: # dates[1] - high correlation to trigger alert
                corr_matrix_daily = pd.DataFrame({
                    'XLU': [1.0, 0.9, 0.9], 'SPY': [0.9, 1.0, 0.9], 'GLD': [0.9, 0.9, 1.0]
                }, index=['XLU', 'SPY', 'GLD'], columns=['XLU', 'SPY', 'GLD'])

            for col_name in ['XLU', 'SPY', 'GLD']:
                for row_name in ['XLU', 'SPY', 'GLD']:
                    data_for_rolling.append({
                        'date': d,
                        'level_1': col_name,
                        'level_2': row_name,
                        'correlation': corr_matrix_daily.loc[row_name, col_name]
                    })
        mock_rolling_corr_df = pd.DataFrame(data_for_rolling)
        mock_rolling_corr_df = mock_rolling_corr_df.set_index(['date', 'level_1'])
        mock_rolling_corr_series = mock_rolling_corr_df.groupby(level=[0,1]).apply(lambda x: pd.Series(x['correlation'].values, index=x['level_2']))

        alert_triggered, messages = detect_convergence(mock_rolling_corr_series, ['XLU', 'GLD'], 'SPY', 0.85) # Threshold for alert
        self.assertTrue(alert_triggered)
        self.assertIsNotNone(messages)
        self.assertIn("ALERT on 2026-01-02", messages[0])

    def test_detect_convergence_no_alert(self):
        # Create a mock rolling correlation matrix where correlation is low
        dates = pd.to_datetime(pd.date_range(start='2026-01-01', periods=2))
        data_for_rolling = []
        for d in dates:
            for i_col, col_name in enumerate(['XLU', 'SPY', 'GLD']):
                for i_row, row_name in enumerate(['XLU', 'SPY', 'GLD']):
                    data_for_rolling.append({
                        'date': d,
                        'level_1': col_name,
                        'level_2': row_name,
                        'correlation': 0.1 if col_name != row_name else 1.0 # Low correlation
                    })
        mock_rolling_corr_df = pd.DataFrame(data_for_rolling)
        mock_rolling_corr_df = mock_rolling_corr_df.set_index(['date', 'level_1'])
        mock_rolling_corr_series = mock_rolling_corr_df.groupby(level=[0,1]).apply(lambda x: pd.Series(x['correlation'].values, index=x['level_2']))

        alert_triggered, messages = detect_convergence(mock_rolling_corr_series, ['XLU', 'GLD'], 'SPY', 0.85)
        self.assertFalse(alert_triggered)
        self.assertIsNone(messages)

    def test_detect_convergence_empty_matrix(self):
        alert_triggered, messages = detect_convergence(None, ['XLU', 'GLD'], 'SPY', 0.5)
        self.assertFalse(alert_triggered)
        self.assertIsNone(messages)

    @patch('pandas.DataFrame.to_csv')
    def test_save_snapshot_success(self, mock_to_csv):
        # Create a mock rolling correlation matrix
        dates = pd.to_datetime(pd.date_range(start='2026-01-01', periods=2))
        # To match the actual structure from calculate_rolling_correlation_matrix
        data_for_rolling = []
        for d in dates:
            for i_col, col_name in enumerate(['XLE', 'XLU']):
                for i_row, row_name in enumerate(['XLE', 'XLU']):
                    corr_val = 0.5 if col_name != row_name else 1.0
                    if d == dates[1]: # Higher correlation for the latest date
                        corr_val = 0.6 if col_name != row_name else 1.0
                    data_for_rolling.append({
                        'date': d,
                        'level_1': col_name,
                        'level_2': row_name,
                        'correlation': corr_val
                    })
        mock_rolling_corr_df = pd.DataFrame(data_for_rolling)
        mock_rolling_corr_df = mock_rolling_corr_df.set_index(['date', 'level_1'])
        mock_rolling_corr_series = mock_rolling_corr_df.groupby(level=[0,1]).apply(lambda x: pd.Series(x['correlation'].values, index=x['level_2']))


        save_snapshot(mock_rolling_corr_series, OUTPUT_FILE)
        mock_to_csv.assert_called_once_with(OUTPUT_FILE)

    def test_save_snapshot_empty_matrix(self):
        # The function print() is called inside, so we mock it to avoid actual printing
        with patch('builtins.print') as mock_print:
            save_snapshot(None, OUTPUT_FILE)
            mock_print.assert_called_with("No correlation matrix to save.")

    # Regression Test: Full script run simulation (without actual DB or file writing)
    @patch('drift_monitor.get_returns_from_db')
    @patch('drift_monitor.calculate_rolling_correlation_matrix')
    @patch('drift_monitor.detect_convergence')
    @patch('drift_monitor.save_snapshot')
    @patch('builtins.print') # Mock print to avoid cluttering test output
    def test_full_script_regression(self, mock_print, mock_save_snapshot, mock_detect_convergence, mock_calculate_rolling_correlation_matrix, mock_get_returns_from_db):
        # Setup mock returns data
        mock_returns_data = pd.DataFrame({
            'XLE': np.random.rand(100),
            'XLU': np.random.rand(100),
            'SPY': np.random.rand(100)
        })
        mock_returns_data.index = pd.to_datetime(pd.date_range(start='2026-01-01', periods=100))
        mock_returns_data.index.name = 'date'
        mock_get_returns_from_db.return_value = mock_returns_data

        # Setup mock rolling correlation matrix
        dates = pd.to_datetime(pd.date_range(start='2026-01-01', periods=5))
        data_for_rolling = []
        for d in dates:
            for i_col, col_name in enumerate(['XLU', 'SPY', 'GLD']):
                for i_row, row_name in enumerate(['XLU', 'SPY', 'GLD']):
                    data_for_rolling.append({
                        'date': d,
                        'level_1': col_name,
                        'level_2': row_name,
                        'correlation': 0.9 if d == dates[-1] and col_name in ['XLU', 'GLD'] and row_name == 'SPY' else 0.1 # High correlation on last day
                    })
        mock_rolling_corr_df = pd.DataFrame(data_for_rolling)
        mock_rolling_corr_df = mock_rolling_corr_df.set_index(['date', 'level_1'])
        mock_rolling_corr_series = mock_rolling_corr_df.groupby(level=[0,1]).apply(lambda x: pd.Series(x['correlation'].values, index=x['level_2']))

        mock_calculate_rolling_correlation_matrix.return_value = mock_rolling_corr_series

        # Setup mock for detect_convergence to return an alert
        mock_detect_convergence.return_value = (True, ["ALERT detected!"])

        # Call the main function
        main()

        # Assert that all functions were called
        mock_get_returns_from_db.assert_called_once()
        mock_calculate_rolling_correlation_matrix.assert_called_once()
        mock_detect_convergence.assert_called_once()
        mock_save_snapshot.assert_called_once()

if __name__ == '__main__':
    unittest.main()
