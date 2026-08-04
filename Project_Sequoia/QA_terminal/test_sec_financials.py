"""
Unit tests for sec_financials.py XBRL extraction helpers (network-free).

Covers _derive_missing_year_end_quarters: fiscal-year-end quarters that only
exist as 363-day 10-K facts (AAPL FY2025 Q4) are delta-derived so the
quarterly view and TTM math get the correct window.
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sec_financials as sf


def q_row(period, net_income, eps=None, revenue=None):
    r = {'period': period, 'type': 'Q', 'net_income': net_income}
    if eps is not None:
        r['eps_diluted'] = eps
    if revenue is not None:
        r['revenue'] = revenue
    return r


def fy_row(period, net_income, eps=None, revenue=None):
    r = {'period': period, 'type': 'FY', 'net_income': net_income}
    if eps is not None:
        r['eps_diluted'] = eps
    if revenue is not None:
        r['revenue'] = revenue
    return r


class TestDeriveMissingYearEndQuarters(unittest.TestCase):
    def test_derives_missing_year_end_quarter(self):
        # AAPL-like: Q4 (2025-09-27) exists only as a 363-day FY row
        income = [
            q_row('2026-06-27', 26.0, eps=2.0),
            q_row('2026-03-28', 21.0, eps=1.6),
            q_row('2025-12-27', 42.0, eps=3.2),
            fy_row('2025-09-27', 112.01, eps=7.46),   # FY2025 (no Q row)
            q_row('2025-06-28', 23.4, eps=1.57),
            q_row('2025-03-29', 23.6, eps=1.57),
            q_row('2024-12-28', 37.1, eps=2.14),
        ]
        out = sf._derive_missing_year_end_quarters(income)
        derived = [r for r in out if r.get('derived')]
        self.assertEqual(len(derived), 1)
        d = derived[0]
        self.assertEqual(d['period'], '2025-09-27')
        self.assertEqual(d['type'], 'Q')
        # Q4 = FY − (Q1+Q2+Q3) = 112.01 − (37.1+23.6+23.4)
        self.assertAlmostEqual(d['net_income'], 27.91, places=2)
        # EPS: 7.46 − (2.14+1.57+1.57)
        self.assertAlmostEqual(d['eps_diluted'], 2.18, places=2)

    def test_complete_data_noop(self):
        income = [
            q_row('2025-12-27', 42.0),
            q_row('2025-09-27', 27.91),
            q_row('2025-06-28', 23.4),
            q_row('2025-03-29', 23.6),
        ]
        out = sf._derive_missing_year_end_quarters(income)
        self.assertEqual(len(out), len(income))
        self.assertFalse(any(r.get('derived') for r in out))

    def test_under_three_prior_quarters_noop(self):
        # FY row with only 2 prior quarters -> cannot derive (guarded)
        income = [
            fy_row('2025-09-27', 112.0),
            q_row('2025-06-28', 23.4),
            q_row('2025-03-29', 23.6),
        ]
        out = sf._derive_missing_year_end_quarters(income)
        self.assertFalse(any(r.get('derived') for r in out))

    def test_fy_row_skipped_when_q_exists(self):
        # Both FY + Q rows for the same end date -> already complete
        income = [
            fy_row('2025-09-27', 112.0),
            q_row('2025-09-27', 27.91),
            q_row('2025-06-28', 23.4),
        ]
        out = sf._derive_missing_year_end_quarters(income)
        self.assertFalse(any(r.get('derived') for r in out))

    def test_none_metrics_not_derived(self):
        # A metric missing from the FY row must not appear in the derived row
        income = [
            fy_row('2025-09-27', 112.0, eps=7.46),
            q_row('2025-06-28', 23.4, eps=1.57),
            q_row('2025-03-29', 23.6, eps=1.57),
            q_row('2024-12-28', 37.1, eps=2.14),
        ]
        out = sf._derive_missing_year_end_quarters(income)
        d = [r for r in out if r.get('derived')][0]
        self.assertIn('net_income', d)
        self.assertIn('eps_diluted', d)
        self.assertNotIn('revenue', d)  # absent from FY row -> not invented


if __name__ == '__main__':
    unittest.main(verbosity=2)
