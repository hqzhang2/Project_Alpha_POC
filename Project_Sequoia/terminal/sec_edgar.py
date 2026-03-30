"""
SEC EDGAR Financial Data Fetcher
Uses SEC XBRL API to get GAAP-compliant 10-Q and 10-K filings
"""
import requests
import json
from datetime import datetime
from typing import List, Dict, Optional

HEADERS = {"User-Agent": "AlphaTerminal/1.0 research@example.com"}

# XBRL Tags for key financial metrics
INCOME_TAGS = {
    'revenue': 'Revenues',
    'cost_of_revenue': 'CostOfGoodsAndServicesSold',
    'gross_profit': 'GrossProfit',
    'operating_income': 'OperatingIncomeLoss',
    'net_income': 'NetIncomeLoss',
    'eps': 'EarningsPerShare',
    'ebit': 'OperatingIncomeLoss',  # Approximation
}

BALANCE_TAGS = {
    'total_assets': 'Assets',
    'current_assets': 'AssetsCurrent',
    'cash': 'CashAndCashEquivalentsAtCarryingValue',
    'inventory': 'InventoryNet',
    'total_liabilities': 'Liabilities',
    'current_liabilities': 'LiabilitiesCurrent',
    'total_equity': 'StockholdersEquity',
}

CASHFLOW_TAGS = {
    'operating_cf': 'CashAndCashEquivalentsFromOperations',
    'investing_cf': 'CashAndCashEquivalentsFromInvestingActivities',
    'financing_cf': 'CashAndCashEquivalentsFromFinancingActivities',
    'capex': 'PaymentsForCapitalAssets',
    'dividends': 'PaymentsOfDividends',
}

def get_cik(ticker: str) -> Optional[str]:
    """Get CIK for a ticker symbol"""
    url = f"https://efts.sec.gov/LATEST/search-index?q={ticker}&forms=10-Q,10-K&dateRange=custom&startdt=2020-01-01&enddt=2026-12-31"
    resp = requests.get(url, headers=HEADERS, timeout=10)
    data = resp.json()
    
    hits = data.get('hits', {}).get('hits', [])
    for hit in hits:
        names = hit['_source'].get('display_names', [])
        for name in names:
            if ticker.upper() in name:
                ciks = hit['_source'].get('ciks', [])
                if ciks:
                    return ciks[0]
    return None

def get_company_facts(cik: str) -> Dict:
    """Get all XBRL facts for a company"""
    # Pad CIK with leading zeros
    cik_padded = cik.zfill(10)
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_padded}.json"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    return resp.json()

def get_filings(cik: str) -> List[Dict]:
    """Get recent 10-Q and 10-K filings"""
    cik_padded = cik.zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    resp = requests.get(url, headers=HEADERS, timeout=15)
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

def extract_financials(company_facts: Dict, periods: int = 8) -> Dict:
    """Extract financial data from XBRL facts"""
    us_gaap = company_facts.get('facts', {}).get('us-gaap', {})
    
    income = []
    balance = []
    cashflow = []
    
    # Helper to get recent values from a tag
    def get_recent_values(tag, count=periods):
        if tag not in us_gaap:
            return []
        units = us_gaap[tag].get('units', {}).get('USD', [])
        # Sort by end date descending, skip duplicates
        seen_dates = set()
        values = []
        for u in units:
            end = u.get('end')
            if end and end not in seen_dates:
                seen_dates.add(end)
                values.append({'period': end, 'value': u.get('val')})
        return sorted(values, key=lambda x: x['period'], reverse=True)[:count]
    
    # Get income statement
    for metric, tag in INCOME_TAGS.items():
        values = get_recent_values(tag, periods)
        for v in values:
            # Find or create entry by period
            existing = next((e for e in income if e['period'] == v['period']), None)
            if not existing:
                income.append({'period': v['period'], 'type': 'Q', metric: v['value']})
            else:
                existing[metric] = v['value']
    
    # Get balance sheet
    for metric, tag in BALANCE_TAGS.items():
        values = get_recent_values(tag, periods)
        for v in values:
            existing = next((e for e in balance if e['period'] == v['period']), None)
            if not existing:
                balance.append({'period': v['period'], 'type': 'Q', metric: v['value']})
            else:
                existing[metric] = v['value']
    
    # Get cash flow
    for metric, tag in CASHFLOW_TAGS.items():
        values = get_recent_values(tag, periods)
        for v in values:
            existing = next((e for e in cashflow if e['period'] == v['period']), None)
            if not existing:
                cashflow.append({'period': v['period'], 'type': 'Q', metric: v['value']})
            else:
                existing[metric] = v['value']
    
    # Sort all by period
    income.sort(key=lambda x: x['period'], reverse=True)
    balance.sort(key=lambda x: x['period'], reverse=True)
    cashflow.sort(key=lambda x: x['period'], reverse=True)
    
    return {'income': income, 'balance': balance, 'cashflow': cashflow}

def fetch_financials(ticker: str, periods: int = 8) -> Dict:
    """Main function to fetch financials for a ticker"""
    # Get CIK
    cik = get_cik(ticker)
    if not cik:
        return {'error': f'Could not find CIK for {ticker}'}
    
    # Get company facts
    company_facts = get_company_facts(cik)
    
    # Get filings info
    filings = get_filings(cik)
    
    # Extract financials
    financials = extract_financials(company_facts, periods)
    
    return {
        'ticker': ticker,
        'cik': cik,
        'filings': filings,
        **financials
    }

if __name__ == '__main__':
    # Test
    result = fetch_financials('AAPL', periods=4)
    print(json.dumps(result, indent=2, default=str)[:2000])
