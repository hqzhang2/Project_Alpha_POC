"""
SEC EDGAR Financial Data Fetcher
Uses SEC XBRL API to get GAAP-compliant 10-Q and 10-K filings
"""
import requests
import json
import os
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional

HEADERS = {"User-Agent": "AlphaTerminal/1.0 research@example.com"}

# SEC official ticker->CIK mapping (https://www.sec.gov/files/company_tickers.json).
# Cached to data/ for a day; includes ADR tickers (BABA, TSM, BHP, ...).
_TICKERS_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'data', 'company_tickers.json')
_TICKERS_CACHE_TTL = 24 * 3600


def _load_ticker_map():
    """{TICKER: '0000320193'} — official SEC mapping, cached daily.

    Returns {} on total failure (network + no cache); callers fall back to
    the full-text search or to the Yahoo path.
    """
    def parse(data):
        out = {}
        for v in data.values() if isinstance(data, dict) else []:
            t = v.get('ticker')
            if t:
                out[str(t).upper()] = str(v.get('cik_str', '')).zfill(10)
        return out

    if os.path.exists(_TICKERS_CACHE):
        try:
            if time.time() - os.path.getmtime(_TICKERS_CACHE) < _TICKERS_CACHE_TTL:
                with open(_TICKERS_CACHE) as f:
                    return parse(json.load(f))
        except Exception:
            pass
    try:
        resp = requests.get('https://www.sec.gov/files/company_tickers.json',
                            headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            try:
                os.makedirs(os.path.dirname(_TICKERS_CACHE), exist_ok=True)
                with open(_TICKERS_CACHE, 'w') as f:
                    json.dump(data, f)
            except Exception:
                pass
            return parse(data)
    except Exception as e:
        print(f"Error fetching company_tickers.json: {e}")
    # Fall back to a stale cache if present
    if os.path.exists(_TICKERS_CACHE):
        try:
            with open(_TICKERS_CACHE) as f:
                return parse(json.load(f))
        except Exception:
            pass
    return {}

# XBRL Tags for key financial metrics (from actual 10-Q/10-K filings)
INCOME_TAGS = {
    'revenue': ['RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues', 'SalesRevenueNet'],
    'cost_of_revenue': ['CostOfGoodsAndServicesSold', 'CostOfRevenue'],
    'gross_profit': 'GrossProfit',
    'rd_expense': 'ResearchAndDevelopmentExpense',
    'sga_expense': 'SellingGeneralAndAdministrativeExpense',
    'operating_expenses': 'OperatingExpenses',
    'operating_income': 'OperatingIncomeLoss',
    'other_income': 'OtherIncomeExpenseNet',
    'income_before_tax': 'IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest',
    'provision_for_tax': 'IncomeTaxExpenseBenefit',
    'net_income': 'NetIncomeLoss',
    'eps_basic': 'EarningsPerShareBasic',
    'eps_diluted': 'EarningsPerShareDiluted',
    'weighted_shares': 'WeightedAverageNumberOfSharesOutstandingBasic',
    'diluted_shares': 'WeightedAverageNumberOfDilutedSharesOutstanding',
    'stock_comp': ['AllocatedShareBasedCompensationExpense', 'ShareBasedCompensation'],
    'depreciation_and_amortization': ['DepreciationDepletionAndAmortization', 'DepreciationAndAmortization'],
}

BALANCE_TAGS = {
    # Assets
    'cash': 'CashAndCashEquivalentsAtCarryingValue',
    'marketable_securities': 'MarketableSecuritiesCurrent',
    'accounts_receivable': 'AccountsReceivableNetCurrent',
    'vendor_receivables': 'NontradeReceivablesCurrent',
    'inventory': 'InventoryNet',
    'current_assets': 'AssetsCurrent',
    'ppe': 'PropertyPlantAndEquipmentNet',
    'ppe_gross': 'PropertyPlantAndEquipmentGross',
    'accumulated_depreciation': 'AccumulatedDepreciationDepletionAndAmortizationPropertyPlantAndEquipment',
    'goodwill': 'Goodwill',
    'intangibles': 'IntangibleAssetsNet',
    'other_assets': 'OtherAssetsNoncurrent',
    'total_assets': 'Assets',
    # Liabilities
    'accounts_payable': ['AccountsPayableCurrent', 'AccountsPayableCurrentAndNoncurrent'],
    'deferred_revenue': 'DeferredRevenueCurrent',
    'deferred_revenue_noncurrent': 'DeferredRevenueNoncurrent',
    'accrued_liabilities': 'AccruedLiabilitiesCurrent',
    'short_term_debt': ['CommercialPaper', 'ShortTermBorrowings'],
    'current_portion_lt_debt': 'LongTermDebtCurrent',
    'current_liabilities': 'LiabilitiesCurrent',
    'term_debt': 'LongTermDebtNoncurrent',
    'total_liabilities': 'Liabilities',
    # Equity
    'common_stock': 'CommonStockValue',
    'retained_earnings': 'RetainedEarningsAccumulatedDeficit',
    'total_equity': 'StockholdersEquity',
    'shares_outstanding': 'CommonStockSharesOutstanding',
}

CASHFLOW_TAGS = {
    'operating_cf': 'NetCashProvidedByUsedInOperatingActivities',
    'depreciation': 'Depreciation',
    'amortization': 'AmortizationOfIntangibleAssets',
    'stock_compensation': 'EmployeeServiceShareBasedCompensationCashFlowEffectCashUsedToSettleAwards',
    'working_capital_change': 'IncreaseDecreaseInAccountsReceivable',
    'investing_cf': 'NetCashProvidedByUsedInInvestingActivities',
    'capex': 'PaymentsToAcquirePropertyPlantAndEquipment',
    'purchases_securities': 'PaymentsToAcquireMarketableSecurities',
    'sales_securities': 'ProceedsFromSalesOfMarketableSecurities',
    'acquisitions': 'PaymentsToAcquireBusinessesNetOfCashAcquired',
    'financing_cf': 'NetCashProvidedByUsedInFinancingActivities',
    'dividends': 'PaymentsOfDividends',
    'share_repurchases': 'PaymentsForRepurchaseOfCommonStock',
    'debt_repayments': 'RepaymentsOfTermDebt',
    'debt_issuances': 'ProceedsFromIssuanceOfTermDebt',
    'interest_paid': 'InterestPaid',
    'taxes_paid': 'IncomeTaxesPaidNet',
}

import psycopg2

DB_CONN = "dbname=project_alpha user=chuck host=localhost"

def get_watchlist():
    try:
        conn = psycopg2.connect(DB_CONN)
        cur = conn.cursor()
        cur.execute("SELECT ticker FROM financial_watchlist ORDER BY added_date DESC")
        rows = cur.fetchall()
        conn.close()
        return [{'ticker': r[0]} for r in rows]
    except Exception as e:
        print(f"Error getting watchlist: {e}")
        return []

def add_to_watchlist(ticker):
    try:
        conn = psycopg2.connect(DB_CONN)
        cur = conn.cursor()
        # First ensure ticker exists in financial_tickers (or add with placeholder)
        cur.execute("INSERT INTO financial_tickers (ticker, name) VALUES (%s, %s) ON CONFLICT (ticker) DO NOTHING", 
                    (ticker, ticker))
        conn.commit()
        # Now add to watchlist
        cur.execute("INSERT INTO financial_watchlist (ticker) VALUES (%s) ON CONFLICT (ticker) DO NOTHING", (ticker,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error adding to watchlist: {e}")

def remove_from_watchlist(ticker):
    try:
        conn = psycopg2.connect(DB_CONN)
        cur = conn.cursor()
        cur.execute("DELETE FROM financial_watchlist WHERE ticker = %s", (ticker,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error removing from watchlist: {e}")

def get_cik(ticker: str) -> Optional[str]:
    """Get CIK for a ticker symbol from SEC.

    Primary: official company_tickers.json mapping (cached daily — includes
    ADR tickers). Fallback: full-text search across 10-Q/10-K/20-F/6-K
    filings (foreign private issuers file 20-F/6-K, never 10-Q/10-K).
    """
    ticker = (ticker or '').strip().upper()
    if not ticker:
        return None
    tm = _load_ticker_map()
    cik = tm.get(ticker)
    if cik:
        return cik
    return _get_cik_search(ticker)


def _get_cik_search(ticker: str) -> Optional[str]:
    """Legacy fallback: SEC full-text search for the ticker symbol."""
    url = (f"https://efts.sec.gov/LATEST/search-index?q={ticker}"
           f"&forms=10-Q,10-K,20-F,6-K&dateRange=custom"
           f"&startdt=2020-01-01&enddt=2026-12-31")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            hits = data.get('hits', {}).get('hits', [])
            for hit in hits:
                names = hit['_source'].get('display_names', [])
                for name in names:
                    if ticker.upper() in name.upper():
                        ciks = hit['_source'].get('ciks', [])
                        if ciks:
                            return ciks[0]
    except Exception as e:
        print(f"Error getting CIK: {e}")
    return None

def get_company_facts(cik: str) -> Dict:
    """Get all XBRL facts for a company"""
    cik_padded = cik.zfill(10)
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_padded}.json"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    if resp.status_code == 200:
        return resp.json()
    return {}

def get_filings_list(cik: str) -> List[Dict]:
    """Get recent 10-Q and 10-K filings"""
    cik_padded = cik.zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    
    if resp.status_code != 200:
        return []
    
    data = resp.json()
    filings = []
    recent = data.get('filings', {}).get('recent', {})
    forms = recent.get('form', [])
    dates = recent.get('filingDate', [])
    
    for form, filing_date in zip(forms, dates):
        if '10-Q' in form or '10-K' in form:
            filings.append({
                'form': form,
                'filing_date': filing_date,
                'type': 'Q' if '10-Q' in form else 'FY'
            })
    
    return filings

def extract_quarterly_data(us_gaap: Dict, tag: str, periods: int, unit: str = 'USD') -> List[Dict]:
    """Extract quarterly data, handling overlapping cumulative periods"""
    if tag not in us_gaap:
        return []
    
    units_data = us_gaap[tag].get('units', {})
    
    # Try requested unit first, then fall back to other common units
    available_units = list(units_data.keys())
    if unit not in units_data:
        # Try common alternatives
        alternatives = {
            'USD': ['USD/shares', 'shares', 'USD'],
            'USD/shares': ['USD', 'shares', 'USD/shares'],
            'shares': ['shares', 'USD', 'USD/shares'],
        }
        for alt in alternatives.get(unit, [unit]):
            if alt in units_data:
                unit = alt
                break
    
    if unit not in units_data:
        return []
    
    units = units_data[unit]
    
    # Group by end date and deduplicate
    by_end = {}
    for u in units:
        end = u.get('end')
        if end and end not in by_end:
            by_end[end] = u.get('val')
    
    # Sort by end date descending
    sorted_dates = sorted(by_end.keys(), reverse=True)
    
    # Extract quarterly (3-month) periods
    results = []
    for end_date in sorted_dates:
        # Try to find corresponding start date
        found_durations = []
        for u in units:
            if u.get('end') == end_date:
                if not u.get('start'):
                    found_durations.append({'u': u, 'days': 0})
                else:
                    start_dt = datetime.strptime(u.get('start'), '%Y-%m-%d')
                    end_dt = datetime.strptime(end_date, '%Y-%m-%d')
                    current_duration = (end_dt - start_dt).days
                    found_durations.append({'u': u, 'days': current_duration})
        
        if not found_durations:
            continue

        # For Income/Cashflow (days > 0), we ONLY want Q (~90 days) OR FY (~365 days)
        # We also need to be careful for balance sheet items (days == 0)
        # Check if any duration is ~90 days
        has_q = any(80 <= d['days'] <= 100 for d in found_durations)
        has_fy = any(355 <= d['days'] <= 380 for d in found_durations)
        is_balance_sheet = any(d['days'] == 0 for d in found_durations)

        best_u = None
        duration_days = -1
        form_type = 'Q'

        if is_balance_sheet:
            # For balance sheet, prioritize the one with days=0
            best_u = next(d['u'] for d in found_durations if d['days'] == 0)
            duration_days = 0
            # If form 10-K exists for this date (even if the specific unit isn't marked 10-K), it's an annual period
            is_annual_date = any('10-K' in (d['u'].get('form') or '') for d in found_durations)
            form_type = 'FY' if is_annual_date else 'Q'
        elif has_q:
            # Prioritize Q for income statement
            best_u = next(d['u'] for d in found_durations if 80 <= d['days'] <= 100)
            duration_days = (datetime.strptime(end_date, '%Y-%m-%d') - datetime.strptime(best_u['start'], '%Y-%m-%d')).days
            form_type = 'Q'
        elif has_fy:
            # Fallback to FY if no Q exists for this end_date
            best_u = next(d['u'] for d in found_durations if 355 <= d['days'] <= 380)
            duration_days = (datetime.strptime(end_date, '%Y-%m-%d') - datetime.strptime(best_u['start'], '%Y-%m-%d')).days
            form_type = 'FY'
        else:
            # Skip 6-month or 9-month cumulative data
            continue
        
        results.append({
            'period': end_date,
            'value': best_u.get('val'),
            'type': form_type,
            'days': duration_days
        })
    
    # Deduplicate results by end_date
    final_results = []
    seen_dates = set()
    for res in results:
        if res['period'] not in seen_dates:
            final_results.append(res)
            seen_dates.add(res['period'])
            
    return final_results[:periods]

def _get_stock_info(ticker: str) -> Dict:
    """Minimal market info (price, shares, market cap) for valuation metrics."""
    try:
        import yfinance as yf
        i = yf.Ticker(ticker).info
        return {
            'name': i.get('shortName', ticker),
            'price': i.get('currentPrice') or i.get('regularMarketPrice'),
            'market_cap': i.get('marketCap'),
            'shares_outstanding': i.get('sharesOutstanding'),
        }
    except Exception as e:
        print(f"Error fetching stock info for {ticker}: {e}")
        return {'name': ticker}


def calculate_graham_metrics(income: List[Dict], balance: List[Dict],
                             cashflow: List[Dict], stock_info: Dict = None,
                             ticker: str = None) -> Dict:
    """Valuation metrics based on Intelligent Investor principles.

    Delegates to fundamentals.py — the single source of truth shared with
    the Yahoo fallback path. Definitions (per Graham):
      - D/E uses short+long-term debt / equity (not total liabilities)
      - Graham Number uses TTM EPS (sum of last 4 quarters / last FY)
      - per-share metrics are ADR-ratio adjusted for foreign listings
    """
    import fundamentals
    return fundamentals.calculate_graham_metrics(
        income, balance, cashflow, stock_info or {}, ticker)

def _derive_missing_year_end_quarters(income: List[Dict]) -> List[Dict]:
    """Delta-derive fiscal-year-end quarters missing as standalone 90-day
    filings (e.g. AAPL FY2025 Q4 exists in companyfacts only as a 363-day
    10-K fact — no 90-day unit, so the Q filter would drop it).

    For each FY row whose end date has NO 'Q' row: Q4 = FY − sum(that FY's
    three most recent quarters). Emitted as a 'Q' row so both the quarterly
    display and TTM math get the correct window. No-op when data is complete
    or fewer than 3 prior quarters exist.
    """
    q_rows = sorted([r for r in income if r.get('type') == 'Q'],
                    key=lambda r: r['period'], reverse=True)
    q_ends = {r['period'] for r in q_rows}
    out = list(income)
    for fy in [r for r in income if r.get('type') == 'FY']:
        fy_end = fy['period']
        if fy_end in q_ends:
            continue
        prior = [r for r in q_rows if r['period'] < fy_end][:3]
        if len(prior) < 3:
            continue
        row = {'period': fy_end, 'type': 'Q', 'derived': True}
        for key in _DERIVABLE_KEYS:
            fv = fy.get(key)
            if fv is None:
                continue
            qsum = sum((p.get(key) or 0) for p in prior)
            if qsum or fv:
                row[key] = round(fv - qsum, 4)
        out.append(row)
    return out


# Period-flow metrics that delta-derive across a fiscal year. Point-in-time
# items (shares outstanding) and ratios are intentionally excluded.
_DERIVABLE_KEYS = [
    'revenue', 'cost_of_revenue', 'gross_profit', 'rd_expense', 'sga_expense',
    'operating_expenses', 'operating_income', 'other_income',
    'income_before_tax', 'provision_for_tax', 'net_income',
    'eps_basic', 'eps_diluted', 'stock_comp', 'depreciation_and_amortization',
]


def fetch_financials(ticker: str, periods: int = 8, period_type: str = 'Q') -> Dict:
    """Main function to fetch financials for a ticker from SEC EDGAR"""
    # Get CIK
    cik = get_cik(ticker)
    if not cik:
        return {'error': f'Could not find CIK for {ticker}'}
    
    # Get company facts
    company_facts = get_company_facts(cik)
    us_gaap = company_facts.get('facts', {}).get('us-gaap', {})
    
    # Get filings info
    filings = get_filings_list(cik)
    
    # Extract quarterly data for each metric
    income = {}
    balance = {}
    cashflow = {}
    
    for metric, tags in INCOME_TAGS.items():
        tag_list = [tags] if isinstance(tags, str) else tags
        unit = 'USD'
        if 'eps' in metric or 'shares' in metric:
            unit = 'USD/shares' if 'eps' in metric else 'shares'
        
        for tag in tag_list:
            data = extract_quarterly_data(us_gaap, tag, periods * 4, unit)
            if data:
                for d in data:
                    period = d['period']
                    if period not in income:
                        income[period] = {'period': period, 'type': d['type']}
                    if metric not in income[period] or income[period][metric] is None:
                        income[period][metric] = d['value']
    
    for metric, tags in BALANCE_TAGS.items():
        tag_list = [tags] if isinstance(tags, str) else tags
        unit = 'shares' if 'shares' in metric else 'USD'
        for tag in tag_list:
            data = extract_quarterly_data(us_gaap, tag, periods * 2, unit)
            if data:
                for d in data:
                    period = d['period']
                    if period not in balance:
                        balance[period] = {'period': period, 'type': d['type']}
                    if metric not in balance[period] or balance[period][metric] is None:
                        balance[period][metric] = d['value']
    
    for metric, tag in CASHFLOW_TAGS.items():
        data = extract_quarterly_data(us_gaap, tag, periods * 4)
        for d in data:
            period = d['period']
            if period not in cashflow:
                cashflow[period] = {'period': period, 'type': d['type']}
            cashflow[period][metric] = d['value']
    
    # Convert to lists and sort
    income_list = sorted(income.values(), key=lambda x: x['period'], reverse=True)
    balance_list = sorted(balance.values(), key=lambda x: x['period'], reverse=True)
    cashflow_list = sorted(cashflow.values(), key=lambda x: x['period'], reverse=True)

    # Delta-derive fiscal-year-end quarters missing as standalone 90-day
    # filings (AAPL FY2025 Q4) BEFORE the type filter drops FY rows.
    income_list = _derive_missing_year_end_quarters(income_list)
    income_list = sorted(income_list, key=lambda x: x['period'], reverse=True)
    
    # Calculate margins and per-share metrics
    for inc in income_list:
        rev = inc.get('revenue', 0)
        if rev and rev > 0:
            if inc.get('gross_profit'):
                inc['gross_margin_pct'] = round(inc['gross_profit'] / rev * 100, 2)
            if inc.get('operating_income'):
                inc['operating_margin_pct'] = round(inc['operating_income'] / rev * 100, 2)
            if inc.get('net_income'):
                inc['net_margin_pct'] = round(inc['net_income'] / rev * 100, 2)
    
    # Calculate derived balance sheet metrics
    for bal in balance_list:
        assets = bal.get('total_assets', 0)
        equity = bal.get('total_equity', 0)
        if assets and equity:
            st_debt = bal.get('short_term_debt', 0) or 0
            cur_lt_debt = bal.get('current_portion_lt_debt', 0) or 0
            lt_debt = bal.get('term_debt', 0) or 0
            total_debt = st_debt + cur_lt_debt + lt_debt
            bal['debt_to_equity'] = round(total_debt / equity, 2) if equity else None
            bal['assets_to_equity'] = round(assets / equity, 2) if equity else None
        
        current_assets = bal.get('current_assets', 0)
        current_liab = bal.get('current_liabilities', 0)
        if current_assets and current_liab:
            bal['current_ratio'] = round(current_assets / current_liab, 2) if current_liab else None
    
    # Calculate cash flow metrics
    for cf in cashflow_list:
        ocf = cf.get('operating_cf', 0)
        capex = cf.get('capex', 0)
        if ocf and capex is not None:
            cf['free_cf'] = ocf - abs(capex)
            cf['fcf_to_ocf'] = round(cf['free_cf'] / ocf * 100, 1) if ocf else None
        
        period = cf['period']
        inc_match = next((i for i in income_list if i['period'] == period), None)
        if inc_match:
            if cf.get('depreciation') is None:
                cf['depreciation'] = inc_match.get('depreciation_and_amortization')
            if cf.get('stock_compensation') is None:
                cf['stock_compensation'] = inc_match.get('stock_comp')
    
    # Filter by period type
    if period_type == 'Q':
        income_list = [i for i in income_list if i['type'] == 'Q']
        balance_list = [b for b in balance_list] 
        cashflow_list = [c for c in cashflow_list if c['type'] == 'Q']
        
        income_list = income_list[:periods]
        balance_list = balance_list[:periods]
        cashflow_list = cashflow_list[:periods]
    elif period_type == 'FY':
        income_list = [i for i in income_list if i['type'] == 'FY'][:periods]
        balance_list = [b for b in balance_list if b['type'] == 'FY'][:periods]
        cashflow_list = [c for c in cashflow_list if c['type'] == 'FY'][:periods]
    else:
        income_list = income_list[:periods]
        balance_list = balance_list[:periods]
        cashflow_list = cashflow_list[:periods]
    
    # Valuation Metrics
    info = _get_stock_info(ticker)
    metrics = calculate_graham_metrics(income_list, balance_list,
                                       cashflow_list, info, ticker)

    return {
        'ticker': ticker.upper(),
        'source': 'SEC EDGAR (XBRL)',
        'cik': cik,
        'filings': filings,
        'income': income_list,
        'balance': balance_list,
        'cashflow': cashflow_list,
        'metrics': metrics,
        'info': info,
    }

if __name__ == '__main__':
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else 'AAPL'
    result = fetch_financials(ticker, periods=4)
    print(json.dumps(result, indent=2, default=str)[:2000])
