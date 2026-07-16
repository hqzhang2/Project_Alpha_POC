"""Yahoo Finance backup financial data"""
import yfinance as yf
import pandas as pd

def get_financials(ticker, periods=8):
    """Get financials from Yahoo Finance"""
    try:
        t = yf.Ticker(ticker)
        
        # Get income statement
        income = t.income_stmt
        if income.empty:
            income = t.quarterly_income_stmt
        
        # Get balance sheet
        balance = t.balance_sheet
        if balance.empty:
            balance = t.quarterly_balance_sheet
            
        # Get cash flow
        cashflow = t.cashflow
        if cashflow.empty:
            cashflow = t.quarterly_cashflow
            
        return {
            'income': _format_income(income, periods),
            'balance': _format_balance(balance, periods),
            'cashflow': _format_cashflow(cashflow, periods)
        }
    except Exception as e:
        print(f"Yahoo finance error for {ticker}: {e}")
        return {'income': [], 'balance': [], 'cashflow': []}

def _format_income(df, periods):
    """Format income statement"""
    if df.empty:
        return []
    
    # Transpose so periods are rows
    data = df.T.reset_index()
    data.columns = ['period'] + list(df.index)
    
    # Map to our schema
    result = []
    for _, row in data.head(periods).iterrows():
        period = str(row['period'])[:10]
        result.append({
            'period': period,
            'type': 'Q' if 'Q' in period else 'FY',
            'revenue': row.get('Total Revenue'),
            'cost_of_revenue': row.get('Cost of Revenue'),
            'gross_profit': row.get('Gross Profit'),
            'operating_income': row.get('Operating Income'),
            'net_income': row.get('Net Income'),
            'eps_basic': row.get('Basic EPS'),
            'eps_diluted': row.get('Diluted EPS'),
            'source': 'yahoo'
        })
    return result

def _format_balance(df, periods):
    """Format balance sheet"""
    if df.empty:
        return []
    
    data = df.T.reset_index()
    data.columns = ['period'] + list(df.index)
    
    # Map common fields
    result = []
    for _, row in data.head(periods).iterrows():
        period = str(row['period'])[:10]
        result.append({
            'period': period,
            'type': 'Q' if 'Q' in period else 'FY',
            'cash': row.get('Cash And Cash Equivalents'),
            'total_assets': row.get('Total Assets'),
            'current_assets': row.get('Current Assets'),
            'total_liabilities': row.get('Total Liabilities'),
            'current_liabilities': row.get('Current Liabilities'),
            'total_equity': row.get('Total Stockholder Equity'),
            'term_debt': row.get('Long Term Debt'),
            'source': 'yahoo'
        })
    return result

def _format_cashflow(df, periods):
    """Format cash flow statement"""
    if df.empty:
        return []
    
    data = df.T.reset_index()
    data.columns = ['period'] + list(df.index)
    
    result = []
    for _, row in data.head(periods).iterrows():
        period = str(row['period'])[:10]
        result.append({
            'period': period,
            'type': 'Q' if 'Q' in period else 'FY',
            'operating_cf': row.get('Operating Cash Flow'),
            'capital_expenditure': row.get('Capital Expenditure'),
            'free_cf': row.get('Free Cash Flow'),
            'depreciation': row.get('Depreciation Amortization Depletion'),
            'stock_compensation': row.get('Stock Based Compensation'),
            'source': 'yahoo'
        })
    return result
