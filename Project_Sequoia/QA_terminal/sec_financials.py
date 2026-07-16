"""
SEC EDGAR Financial Data Fetcher
Uses SEC XBRL API to get GAAP-compliant 10-Q and 10-K filings
"""
import requests
import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional

HEADERS = {"User-Agent": "AlphaTerminal/1.0 research@example.com"}

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
    """Get CIK for a ticker symbol from SEC"""
    url = f"https://efts.sec.gov/LATEST/search-index?q={ticker}&forms=10-Q,10-K&dateRange=custom&startdt=2020-01-01&enddt=2026-12-31"
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

def calculate_graham_metrics(income: List[Dict], balance: List[Dict], cashflow: List[Dict]) -> Dict:
    """Calculate valuation metrics based on Intelligent Investor principles"""
    m = {}
    if not income or not balance:
        return m
    
    inc = income[0]
    bs = balance[0]
    
    # Fundamental Ratios
    ca = bs.get('current_assets') or 0
    cl = bs.get('current_liabilities') or 0
    equity = bs.get('total_equity') or 0
    total_liab = bs.get('total_liabilities') or 0
    
    m['current_ratio'] = round(ca / cl, 2) if cl else 0
    m['debt_to_equity'] = round(total_liab / equity, 2) if equity else 0
    
    eps = inc.get('eps_diluted') or 0
    # BVPS = Total Equity / Diluted Shares
    shares = inc.get('diluted_shares') or bs.get('shares_outstanding') or 1
    bvps = equity / shares
    
    # Graham Number = sqrt(22.5 * EPS * BVPS)
    if eps > 0 and bvps > 0:
        m['graham_number'] = round((22.5 * eps * bvps) ** 0.5, 2)
    else:
        m['graham_number'] = None

    # Scoring (0-10)
    score = 0
    if m['current_ratio'] >= 2.0: score += 2
    elif m['current_ratio'] >= 1.5: score += 1
    
    if m['debt_to_equity'] < 0.5: score += 2
    elif m['debt_to_equity'] < 1.0: score += 1
    
    net_margin = inc.get('net_margin_pct') or 0
    if net_margin > 15: score += 2
    elif net_margin > 10: score += 1
    
    m['valuation_score'] = score
    if score >= 5: m['rating'] = '⭐⭐⭐ Strong Buy'
    elif score >= 3: m['rating'] = '⭐⭐ Buy'
    elif score >= 1: m['rating'] = '⭐ Hold'
    else: m['rating'] = '⚠️ Speculative'
    
    return m

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
    metrics = calculate_graham_metrics(income_list, balance_list, cashflow_list)

    return {
        'ticker': ticker.upper(),
        'source': 'SEC EDGAR (XBRL)',
        'cik': cik,
        'filings': filings,
        'income': income_list,
        'balance': balance_list,
        'cashflow': cashflow_list,
        'metrics': metrics
    }

if __name__ == '__main__':
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else 'AAPL'
    result = fetch_financials(ticker, periods=4)
    print(json.dumps(result, indent=2, default=str)[:2000])
