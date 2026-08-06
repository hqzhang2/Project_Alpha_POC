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


class TestDeltaDeriveQuarters(unittest.TestCase):
    """YTD-cumulative cashflow (US 10-Q convention): Q2/Q3 from cumulative
    deltas, Q4 from the FY row. Income-style standalone rows are no-ops."""

    def _ytd_rows(self):
        return [
            {'period': '2024-12-28', 'type': 'Q', 'days': 90,
             'operating_cf': 40e9, 'capex': -3e9},
            {'period': '2025-03-29', 'type': 'C', 'days': 181,
             'operating_cf': 50e9, 'capex': -5e9},
            {'period': '2025-06-28', 'type': 'C', 'days': 272,
             'operating_cf': 80e9, 'capex': -8e9},
            {'period': '2025-09-27', 'type': 'FY', 'days': 363,
             'operating_cf': 111.5e9, 'capex': -11e9},
        ]

    def test_delta_derives_q2_q3(self):
        out = sf._delta_derive_quarters(self._ytd_rows())
        derived = {r['period']: r for r in out if r.get('derived')}
        self.assertEqual(set(derived), {'2025-03-29', '2025-06-28'})
        self.assertEqual(derived['2025-03-29']['operating_cf'], 10e9)   # 50-40
        self.assertEqual(derived['2025-06-28']['operating_cf'], 30e9)   # 80-50
        self.assertEqual(derived['2025-03-29']['capex'], -2e9)          # -5--3
        # FY row untouched by the delta pass
        fy = [r for r in out if r['type'] == 'FY']
        self.assertEqual(len(fy), 1)

    def test_full_chain_derives_q4_from_fy(self):
        out = sf._derive_missing_year_end_quarters(
            sf._delta_derive_quarters(self._ytd_rows()))
        q4 = [r for r in out if r['period'] == '2025-09-27' and r['type'] == 'Q']
        self.assertEqual(len(q4), 1)
        self.assertTrue(q4[0]['derived'])
        # 111.5 - (40 + 10 + 30) = 31.5
        self.assertAlmostEqual(q4[0]['operating_cf'], 31.5e9)
        self.assertEqual(len([r for r in out if r['type'] == 'Q']), 4)

    def test_no_c_rows_is_noop(self):
        rows = [{'period': '2025-03-29', 'type': 'Q', 'days': 90,
                 'operating_cf': 10e9}]
        self.assertIs(sf._delta_derive_quarters(rows), rows)

    def test_standalone_quarters_year_end_only(self):
        # A filer with standalone Q1-Q3: delta pass no-ops, year-end still
        # derives Q4 = FY - (Q1+Q2+Q3).
        rows = [
            {'period': '2024-12-28', 'type': 'Q', 'days': 91, 'operating_cf': 40e9},
            {'period': '2025-03-29', 'type': 'Q', 'days': 91, 'operating_cf': 10e9},
            {'period': '2025-06-28', 'type': 'Q', 'days': 91, 'operating_cf': 30e9},
            {'period': '2025-09-27', 'type': 'FY', 'days': 363, 'operating_cf': 111.5e9},
        ]
        out = sf._derive_missing_year_end_quarters(
            sf._delta_derive_quarters(rows))
        q4 = [r for r in out if r['period'] == '2025-09-27' and r['type'] == 'Q']
        self.assertTrue(q4[0]['derived'])
        self.assertAlmostEqual(q4[0]['operating_cf'], 31.5e9)


if __name__ == '__main__':
    unittest.main()
