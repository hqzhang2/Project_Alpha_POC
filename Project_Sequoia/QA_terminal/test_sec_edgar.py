import unittest
from unittest.mock import patch, MagicMock
import sec_edgar

class TestSecEdgar(unittest.TestCase):

    @patch('requests.get')
    def test_get_cik_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            'hits': {'hits': [{'_source': {'display_names': ['Apple Inc. (AAPL) (CIK 0000320193)'], 'ciks': ['0000320193']}}]}
        }
        mock_get.return_value = mock_resp
        self.assertEqual(sec_edgar.get_cik('AAPL'), '0000320193')

    @patch('requests.get')
    def test_get_cik_not_found(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'hits': {'hits': []}}
        mock_get.return_value = mock_resp
        self.assertIsNone(sec_edgar.get_cik('INVALID'))

    @patch('sec_edgar.get_cik')
    def test_fetch_financials_missing_cik(self, mock_get_cik):
        mock_get_cik.return_value = None
        result = sec_edgar.fetch_financials('INVALID')
        self.assertEqual(result['error'], 'Could not find CIK for INVALID')

    @patch('requests.get')
    def test_get_company_facts(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'facts': {'us-gaap': {'Revenues': {}}}}
        mock_get.return_value = mock_resp
        facts = sec_edgar.get_company_facts('0000320193')
        self.assertEqual(facts, {'facts': {'us-gaap': {'Revenues': {}}}})

    @patch('requests.get')
    def test_get_filings(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'filings': {'recent': {'accessionNumber': ['123']}}}
        mock_get.return_value = mock_resp
        filings = sec_edgar.get_filings('0000320193')
        # The function returns the list/dict from 'recent' or [] on error
        self.assertEqual(filings, [])

    def test_extract_financials_empty(self):
        result = sec_edgar.extract_financials({}, periods=4)
        self.assertEqual(result['income'], [])
        self.assertEqual(result['balance'], [])
        self.assertEqual(result['cashflow'], [])

    @patch('sec_edgar.get_cik')
    @patch('sec_edgar.get_company_facts')
    @patch('sec_edgar.get_filings')
    @patch('sec_edgar.extract_financials')
    def test_fetch_financials_success(self, mock_extract, mock_filings, mock_facts, mock_cik):
        mock_cik.return_value = '0000320193'
        mock_facts.return_value = {}
        mock_filings.return_value = {}
        mock_extract.return_value = {'income': [], 'balance': [], 'cashflow': []}
        
        result = sec_edgar.fetch_financials('AAPL')
        self.assertEqual(result['ticker'], 'AAPL')
        self.assertEqual(result['cik'], '0000320193')


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)


    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        import sec_edgar
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)

if __name__ == '__main__':
    unittest.main()

    def test_extract_financials_with_data(self):
        # Mock company facts structure
        facts = {
            'facts': {
                'us-gaap': {
                    'Revenues': {
                        'units': {
                            'USD': [
                                {'val': 1000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'},
                                {'val': 900, 'end': '2023-09-30', 'form': '10-Q', 'frame': 'CY2023Q3'}
                            ]
                        }
                    },
                    'Assets': {
                        'units': {
                            'USD': [
                                {'val': 5000, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4I'}
                            ]
                        }
                    },
                    'CashAndCashEquivalentsFromOperations': {
                        'units': {
                            'USD': [
                                {'val': 500, 'end': '2023-12-31', 'form': '10-Q', 'frame': 'CY2023Q4'}
                            ]
                        }
                    }
                }
            }
        }
        
        result = sec_edgar.extract_financials(facts, periods=4)
        
        # Test income extraction
        self.assertTrue(len(result['income']) > 0)
        self.assertEqual(result['income'][0]['revenue'], 1000)
        
        # Test balance extraction
        self.assertTrue(len(result['balance']) > 0)
        self.assertEqual(result['balance'][0]['total_assets'], 5000)
        
        # Test cashflow extraction
        self.assertTrue(len(result['cashflow']) > 0)
        self.assertEqual(result['cashflow'][0]['operating_cf'], 500)
