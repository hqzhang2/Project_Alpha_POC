"""
Unit Tests for Financial Analyzer Module
"""
import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def load_fresh():
    """Load module fresh like server does"""
    for mod in list(sys.modules.keys()):
        if 'financials' in mod.lower():
            del sys.modules[mod]
    
    import importlib.util
    spec = importlib.util.spec_from_file_location("financials", "financials.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TestToFloat(unittest.TestCase):
    def test_none(self):
        m = load_fresh()
        self.assertIsNone(m.to_float(None))
    
    def test_float(self):
        m = load_fresh()
        self.assertEqual(m.to_float(123.45), 123.45)


class TestGetFinancials(unittest.TestCase):
    """Test get_financials function"""
    
    def test_returns_dict(self):
        m = load_fresh()
        result = m.get_financials('MSFT', 1, 'Q')
        self.assertIsInstance(result, dict)
    
    def test_has_required_keys(self):
        m = load_fresh()
        result = m.get_financials('MSFT', 1, 'Q')
        for key in ['income', 'balance', 'cashflow', 'metrics', 'info']:
            self.assertIn(key, result)
    
    def test_income_has_data(self):
        m = load_fresh()
        result = m.get_financials('MSFT', 2, 'Q')
        self.assertGreater(len(result['income']), 0)
        self.assertIn('period', result['income'][0])
        self.assertIn('revenue', result['income'][0])
    
    def test_balance_has_data(self):
        m = load_fresh()
        result = m.get_financials('MSFT', 2, 'Q')
        self.assertGreater(len(result['balance']), 0)
        self.assertIn('period', result['balance'][0])
        self.assertIn('total_assets', result['balance'][0])
    
    def test_info_has_name(self):
        m = load_fresh()
        result = m.get_financials('MSFT', 1, 'Q')
        self.assertIn('name', result['info'])
        self.assertIsNotNone(result['info']['name'])
    
    def test_metrics_has_score_and_rating(self):
        m = load_fresh()
        result = m.get_financials('MSFT', 1, 'Q')
        self.assertIn('score', result['metrics'])
        self.assertIn('rating', result['metrics'])
        self.assertIsInstance(result['metrics']['score'], (int, float))
    
    def test_aapl_data(self):
        m = load_fresh()
        result = m.get_financials('AAPL', 2, 'Q')
        
        # Should return data
        self.assertGreater(len(result['income']), 0)
        self.assertGreater(len(result['balance']), 0)
        
        # Should have metrics
        metrics = result['metrics']
        self.assertIn('score', metrics)
        self.assertIn('rating', metrics)
        self.assertIn('current_ratio', metrics)
        self.assertIn('net_margin', metrics)


class TestIntegration(unittest.TestCase):
    """Integration tests"""
    
    def test_multiple_tickers(self):
        """Test multiple tickers"""
        m = load_fresh()
        
        for ticker in ['AAPL', 'MSFT', 'GOOGL']:
            result = m.get_financials(ticker, 2, 'Q')
            self.assertGreater(len(result['income']), 0)
            self.assertIsNotNone(result['info'].get('name'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
