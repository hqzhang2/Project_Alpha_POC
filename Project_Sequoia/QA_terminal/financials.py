"""
Financial Data Module - Graham Analysis
"""
import yfinance as yf
import psycopg2
import pandas as pd

DB_CONN = "dbname=project_alpha user=chuck host=localhost"

def get_financials(ticker, max_periods=10, period_type='Q'):
    import os
    os.environ['DEBUG_FINANCIALS'] = f'{ticker}:{period_type}'
    """Get financial data + Graham analysis
    period_type: 'Q' for quarterly, 'FY' for annual
    """
    stock = yf.Ticker(ticker)
    
    # Get data based on period type
    if period_type == 'FY':
        income_df = stock.income_stmt
        balance_df = stock.balance_sheet
        cashflow_df = stock.cashflow
    else:  # Q or anything else
        income_df = stock.quarterly_income_stmt
        balance_df = stock.quarterly_balance_sheet
        cashflow_df = stock.quarterly_cashflow
    
    # Process income
    income = []
    if not income_df.empty:
        cols = list(income_df.columns[:max_periods])
        for col in cols:
            period = col.strftime('%Y-%m-%d') if hasattr(col, 'strftime') else str(col)[:10]
            row = income_df[col]
            ptype = 'FY' if period_type == 'FY' else 'Q'
            
            income.append({
                'period': period,
                'type': ptype,
                'revenue': to_float(row.get('Total Revenue')),
                'cost_of_revenue': to_float(row.get('Cost Of Revenue')),
                'gross_profit': to_float(row.get('Gross Profit')),
                'operating_income': to_float(row.get('Operating Income')),
                'operating_expenses': to_float(row.get('Operating Expenses')),
                'net_income': to_float(row.get('Net Income')),
                'gaap_net_income': to_float(row.get('Net Income')),  # Use Net Income as GAAP
                'eps': to_float(row.get('Diluted EPS') or row.get('Basic EPS')),
                'ebit': to_float(row.get('EBIT')),
                'ebitda': to_float(row.get('EBITDA'))
            })
    
    # Process balance
    balance = []
    if not balance_df.empty:
        cols = list(balance_df.columns[:max_periods])
        for col in cols:
            period = col.strftime('%Y-%m-%d') if hasattr(col, 'strftime') else str(col)[:10]
            row = balance_df[col]
            ptype = 'FY' if period_type == 'FY' else 'Q'
            
            balance.append({
                'period': period,
                'type': ptype,
                'total_assets': to_float(row.get('Total Assets')),
                'current_assets': to_float(row.get('Current Assets')),
                'cash': to_float(row.get('Cash And Cash Equivalents')),
                'short_term_investments': to_float(row.get('Short-Term Investments')),
                'inventory': to_float(row.get('Inventory')),
                'net_receivables': to_float(row.get('Net Receivables')),
                'total_liabilities': to_float(row.get('Total Liabilities')),
                'current_liabilities': to_float(row.get('Current Liabilities')),
                'payable': to_float(row.get('Payable')),
                'short_term_debt': to_float(row.get('Short-Term Debt')),
                'long_term_debt': to_float(row.get('Long-Term Debt')),
                'total_equity': to_float(row.get('Total Stockholder Equity')),
                'common_stock': to_float(row.get('Common Stock')),
                'additional_paid_in_capital': to_float(row.get('Additional Paid-In Capital')),
                'treasury_stock': to_float(row.get('Treasury Stock')),
                'retained_earnings': to_float(row.get('Retained Earnings')),
                'accumulated_other_comprehensive': to_float(row.get('Accumulated Other Comprehensive Income')),
                'book_value': to_float(row.get('Tangible Book Value'))
            })
    
    # Process cashflow
    cashflow = []
    if not cashflow_df.empty:
        cols = list(cashflow_df.columns[:max_periods])
        for col in cols:
            period = col.strftime('%Y-%m-%d') if hasattr(col, 'strftime') else str(col)[:10]
            row = cashflow_df[col]
            ptype = 'FY' if period_type == 'FY' else 'Q'
            
            cashflow.append({
                'period': period,
                'type': ptype,
                'operating_cf': to_float(row.get('Operating Cash Flow')),
                'investing_cf': to_float(row.get('Investing Cash Flow')),
                'financing_cf': to_float(row.get('Financing Cash Flow')),
                'free_cf': to_float(row.get('Free Cash Flow')),
                'capex': to_float(row.get('Capital Expenditure')),
                'dividends': to_float(row.get('Stock Dividend Paid'))
            })
    
    # Stock info
    info = stock.info
    stock_info = {
        'name': info.get('shortName', ticker),
        'sector': info.get('sector', ''),
        'industry': info.get('industry', ''),
        'price': info.get('currentPrice', info.get('regularMarketPrice', 100)),
        'market_cap': info.get('marketCap'),
        'dividend_yield': info.get('dividendYield'),
        'pe_ratio': info.get('trailingPE') or info.get('peRatio'),
        'trailingPE': info.get('trailingPE'),
    }
    
    # Calculate Graham metrics
    metrics = calculate_graham_metrics(income, balance, cashflow, stock_info)
    
    return {
        'income': income,
        'balance': balance,
        'cashflow': cashflow,
        'metrics': metrics,
        'info': stock_info
    }

def to_float(val):
    if val is None or pd.isna(val):
        return None
    try:
        return float(val)
    except:
        return None

def calculate_graham_metrics(income, balance, cashflow, stock_info):
    m = {}
    if not income or not balance:
        return m
    
    inc = income[0]
    bs = balance[0]
    cf = cashflow[0] if cashflow else {}
    price = stock_info.get('price', 100)
    
    ca = bs.get('current_assets') or 0
    cl = bs.get('current_liabilities') or 0
    equity = bs.get('equity') or 0
    debt = bs.get('debt') or 0
    cash = bs.get('cash') or 0
    
    m['current_ratio'] = ca / cl if cl else 0
    m['debt_equity'] = debt / equity if equity else 0
    m['quick_ratio'] = (cash + (ca - cash)) / cl if cl else 0
    
    ni = inc.get('net_income') or 0
    rev = inc.get('revenue') or 0
    eps = inc.get('eps') or 0
    
    # Use trailing 12 months (TTM) EPS for P/E - sum of last 4 quarters
    if len(income) >= 4:
        m["pe_ratio"] = stock_info.get("pe_ratio") if stock_info.get("pe_ratio") else 0 # fallback
        m['earnings_yield'] = (eps / price * 100) if price else 0
    
    if rev:
        m['gross_margin'] = (inc.get('gross_profit', 0) or 0) / rev * 100
        m['net_margin'] = ni / rev * 100
    
    if len(income) >= 2:
        prev = income[1]
        prev_eps = prev.get('eps') or 0
        if prev_eps and prev_eps != 0 and eps:
            m['eps_growth'] = (eps - prev_eps) / prev_eps * 100
    
    if eps and equity:
        bvps = equity / 1e9
        if bvps > 0:
            m['graham_number'] = (22.5 * eps * bvps) ** 0.5
            m['price_to_graham'] = price / m['graham_number'] if m['graham_number'] else 0
    
    m['ncav'] = ca - (bs.get('total_liabilities') or 0)
    
    score = 0
    if m.get('current_ratio', 0) >= 2.0: score += 2
    elif m.get('current_ratio', 0) >= 1.5: score += 1
    if m.get('debt_equity', 0) < 0.5: score += 2
    elif m.get('debt_equity', 0) < 1.0: score += 1
    if 0 < m.get('pe_ratio', 0) < 15: score += 2
    elif 15 <= m.get('pe_ratio', 0) < 20: score += 1
    if m.get('earnings_yield', 0) > 6.67: score += 2
    elif m.get('earnings_yield', 0) > 4: score += 1
    if m.get('net_margin', 0) > 15: score += 2
    elif m.get('net_margin', 0) > 10: score += 1
    if 0 < m.get('price_to_graham', 0) < 1.0: score += 2
    elif 1.0 <= m.get('price_to_graham', 0) < 1.5: score += 1
    
    m['score'] = score
    if score >= 8: m['rating'] = '⭐⭐⭐ Strong Buy'
    elif score >= 6: m['rating'] = '⭐⭐ Buy'
    elif score >= 4: m['rating'] = '⭐ Hold'
    elif score >= 2: m['rating'] = '⚠️ Speculative'
    else: m['rating'] = '❌ Avoid'
    
    return m

def get_watchlist():
    conn = psycopg2.connect(DB_CONN)
    cur = conn.cursor()
    cur.execute("SELECT ticker FROM financial_watchlist ORDER BY added_date DESC")
    rows = cur.fetchall()
    conn.close()
    return [{'ticker': r[0]} for r in rows]

def add_to_watchlist(ticker):
    conn = psycopg2.connect(DB_CONN)
    cur = conn.cursor()
    cur.execute("INSERT INTO financial_watchlist (ticker) VALUES (%s) ON CONFLICT (ticker) DO NOTHING", (ticker,))
    conn.commit()
    conn.close()

def remove_from_watchlist(ticker):
    conn = psycopg2.connect(DB_CONN)
    cur = conn.cursor()
    cur.execute("DELETE FROM financial_watchlist WHERE ticker = %s", (ticker,))
    conn.commit()
    conn.close()
