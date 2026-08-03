"""
Unit tests for fundamentals.py — shared Graham/Intelligent-Investor metrics.
Pure math only (no network): synthetic statements, verify definitions.
"""
import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fundamentals as f


def q_income(eps_list, rev_list=None, ni_list=None, gp_list=None):
    """Build quarterly income rows: newest first (like providers emit)."""
    rows = []
    n = len(eps_list)
    rev_list = (rev_list or [1000]) * n
    ni_list = (ni_list or [100]) * n
    gp_list = (gp_list or [400]) * n
    for i, eps in enumerate(eps_list):
        rows.append({
            'period': f'2026-Q{i:02d}', 'type': 'Q',
            'revenue': rev_list[i], 'gross_profit': gp_list[i],
            'net_income': ni_list[i], 'eps_diluted': eps,
        })
    return rows


def balance(ca=2000, cl=800, cash=500, receivables=400, equity=3000,
            st_debt=200, lt_debt=800, total_liab=4000, shares=1000):
    return [{
        'period': '2026-Q00', 'type': 'Q',
        'current_assets': ca, 'current_liabilities': cl, 'cash': cash,
        'net_receivables': receivables, 'total_equity': equity,
        'short_term_debt': st_debt, 'long_term_debt': lt_debt,
        'total_liabilities': total_liab, 'shares_outstanding': shares,
    }]


class TestTtmEps(unittest.TestCase):
    def test_sums_last_4_quarters(self):
        eps = f.ttm_eps(q_income([1.0, 2.0, 3.0, 4.0, 5.0]))
        self.assertEqual(eps[0], 10.0)  # 4+3+2+1 (newest first)
        self.assertEqual(eps[1], 'Q')

    def test_falls_back_to_fy_when_under_4_quarters(self):
        rows = [{'period': '2026-03-31', 'type': 'FY', 'eps_diluted': 8.0},
                {'period': '2025-03-31', 'type': 'FY', 'eps_diluted': 7.0}]
        eps = f.ttm_eps(rows)
        self.assertEqual(eps[0], 8.0)
        self.assertEqual(eps[1], 'FY')

    def test_empty_income(self):
        self.assertEqual(f.ttm_eps([]), (0.0, 'NONE'))

    def test_positional_fallback_when_untyped(self):
        rows = [{'period': '2026-01-01', 'eps_diluted': 1.0},
                {'period': '2025-10-01', 'eps_diluted': 2.0},
                {'period': '2025-07-01', 'eps_diluted': 3.0},
                {'period': '2025-04-01', 'eps_diluted': 4.0}]
        self.assertEqual(f.ttm_eps(rows)[0], 10.0)


class TestAdrRatio(unittest.TestCase):
    def test_known_multi_share_adrs(self):
        self.assertEqual(f.derive_adr_ratio('TSM'), 5.0)
        self.assertEqual(f.derive_adr_ratio('BABA'), 8.0)
        self.assertEqual(f.derive_adr_ratio('BHP'), 2.0)
        self.assertEqual(f.derive_adr_ratio('JD'), 2.0)
        self.assertEqual(f.derive_adr_ratio('NTES'), 5.0)

    def test_case_insensitive(self):
        self.assertEqual(f.derive_adr_ratio('tsm'), 5.0)
        self.assertEqual(f.derive_adr_ratio('Baba'), 8.0)

    def test_us_names_and_unknown_default_1(self):
        self.assertEqual(f.derive_adr_ratio('AAPL'), 1.0)
        self.assertEqual(f.derive_adr_ratio('MSFT'), 1.0)
        self.assertEqual(f.derive_adr_ratio('FOO123'), 1.0)
        self.assertEqual(f.derive_adr_ratio(None), 1.0)
        self.assertEqual(f.derive_adr_ratio(''), 1.0)

    def test_info_arg_ignored(self):
        # No reliable ratio signal exists in info (verified live 2026-08);
        # the table is the source of truth.
        self.assertEqual(f.derive_adr_ratio('TSM', {'price': 406}), 5.0)


class TestGrahamMetrics(unittest.TestCase):
    def test_empty_inputs(self):
        self.assertEqual(f.calculate_graham_metrics([], [], []), {})

    def test_debt_to_equity_uses_debt_not_total_liabilities(self):
        # High payables (total_liab big) but low debt -> Graham-clean balance
        bs = balance(ca=2000, cl=800, equity=3000, st_debt=100, lt_debt=200,
                     total_liab=9000)
        m = f.calculate_graham_metrics(q_income([2.0] * 4), bs, [],
                                       {'price': 30})
        self.assertEqual(m['debt_to_equity'], 0.1)   # (100+200)/3000
        self.assertGreaterEqual(m['valuation_score'], 2)  # D/E < 0.5 -> +2

    def test_graham_number_uses_ttm_eps(self):
        # 4 quarters of $1.00 -> TTM $4.00; BVPS = 3000/1000 = 3.00
        m = f.calculate_graham_metrics(q_income([1.0] * 4), balance(), [],
                                       {'price': 30})
        self.assertEqual(m['eps_ttm'], 4.0)
        self.assertEqual(m['bvps'], 3.0)
        # sqrt(22.5 * 4 * 3) = sqrt(270) ~= 16.43
        self.assertAlmostEqual(m['graham_number'], 16.43, places=2)
        # P/E = 30/4 = 7.5, earnings yield = 13.33%
        self.assertEqual(m['pe_ratio'], 7.5)
        self.assertAlmostEqual(m['earnings_yield'], 13.33, places=2)

    def test_adr_ratio_scales_per_share_metrics(self):
        # TSM: ordinary eps $0.40/q -> TTM $1.60; R=5 -> per-ADR $8.00
        inc = q_income([0.4] * 4)
        bs = balance(equity=3.0e9, st_debt=1.0e8, lt_debt=2.0e8, shares=1.0e9)
        info = {'price': 190.0, 'shares_outstanding': 1.0e9}
        m = f.calculate_graham_metrics(inc, bs, [], info, ticker='TSM')
        self.assertEqual(m['adr_ratio'], 5.0)
        self.assertEqual(m['eps_ttm'], 8.0)            # 1.60 * 5
        self.assertEqual(m['bvps'], 15.0)              # 3.00 * 5
        self.assertEqual(m['pe_ratio'], 23.75)         # 190 / 8
        # Without ratio: eps 1.6 -> P/E 118.75 (the ADR value trap)

    def test_us_ratio_does_not_distort(self):
        inc = q_income([2.0] * 4)
        bs = balance(equity=3000, shares=1000)
        info = {'price': 100.0, 'shares_outstanding': 1000}
        m = f.calculate_graham_metrics(inc, bs, [], info, ticker='AAPL')
        self.assertEqual(m['adr_ratio'], 1.0)
        self.assertEqual(m['eps_ttm'], 8.0)
        self.assertEqual(m['bvps'], 3.0)

    def test_unknown_ticker_defaults_to_1(self):
        inc = q_income([1.0] * 4)
        bs = balance(equity=3000, shares=1000)
        info = {'price': 30.0, 'shares_outstanding': 1000}
        m = f.calculate_graham_metrics(inc, bs, [], info, ticker='ZZZZ')
        self.assertEqual(m['adr_ratio'], 1.0)
        self.assertEqual(m['eps_ttm'], 4.0)

    def test_quick_ratio_uses_cash_plus_receivables(self):
        bs = balance(ca=2000, cl=1000, cash=500, receivables=300)
        m = f.calculate_graham_metrics(q_income([1.0] * 4), bs, [],
                                       {'price': 10})
        self.assertEqual(m['current_ratio'], 2.0)
        self.assertEqual(m['quick_ratio'], 0.8)  # (500+300)/1000

    def test_ncav(self):
        bs = balance(ca=2000, total_liab=1500)
        m = f.calculate_graham_metrics(q_income([1.0] * 4), bs, [],
                                       {'price': 10})
        self.assertEqual(m['ncav'], 500)

    def test_rating_bands(self):
        # Score 0 -> Avoid
        bs = balance(ca=100, cl=500, equity=100, st_debt=500, lt_debt=500)
        m = f.calculate_graham_metrics(q_income([0.01] * 4, rev_list=[1000]),
                                       bs, [], {'price': 500})
        self.assertEqual(m['rating'], '❌ Avoid')
        self.assertEqual(m['valuation_score'], 0)

    def test_score_12_strong_buy(self):
        # Cheap, clean, profitable
        inc = q_income([2.0] * 4, rev_list=[1000] * 4, ni_list=[200] * 4,
                       gp_list=[500] * 4)
        bs = balance(ca=3000, cl=1000, equity=3000, st_debt=50, lt_debt=100)
        m = f.calculate_graham_metrics(inc, bs, [], {'price': 10})
        self.assertEqual(m['valuation_score'], 12)
        self.assertEqual(m['rating'], '⭐⭐⭐ Strong Buy')

    def test_no_price_still_computes_balance_metrics(self):
        m = f.calculate_graham_metrics(q_income([1.0] * 4), balance(), [])
        self.assertEqual(m['current_ratio'], 2.5)
        self.assertIsNone(m.get('pe_ratio'))
        self.assertIsNone(m.get('price_to_graham'))
        # Graham number needs no price: sqrt(22.5 * 4 * 3) ~= 16.43
        self.assertAlmostEqual(m['graham_number'], 16.43, places=2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
