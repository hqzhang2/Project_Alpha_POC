"""Yahoo Finance fallback financial data (ADR / foreign-issuer path).

Normalizes statements to the canonical schema consumed by
fundamentals.calculate_graham_metrics() so the fallback path gets the same
valuation scorecard as the SEC EDGAR path.
"""
import yfinance as yf


def get_financials(ticker, periods=8, period_type='Q'):
    """Get financials from Yahoo Finance.

    period_type: 'Q' -> quarterly statements (fall back to annual if the
                 quarterly frame is empty); 'FY' -> annual first.
    Statements are normalized to USD (foreign issuers report in home
    currency — BABA=CNY, TSM=TWD — via fundamentals.usd_per_unit).
    Returns {income, balance, cashflow, info} on the canonical schema.
    """
    try:
        t = yf.Ticker(ticker)

        if period_type == 'FY':
            income, inc_type = _pick(t.income_stmt, t.quarterly_income_stmt, 'FY')
            balance, bal_type = _pick(t.balance_sheet, t.quarterly_balance_sheet, 'FY')
            cashflow, cf_type = _pick(t.cashflow, t.quarterly_cashflow, 'FY')
        else:
            income, inc_type = _pick(t.quarterly_income_stmt, t.income_stmt, 'Q')
            balance, bal_type = _pick(t.quarterly_balance_sheet, t.balance_sheet, 'Q')
            cashflow, cf_type = _pick(t.quarterly_cashflow, t.cashflow, 'Q')

        info = _get_info(t)
        import fundamentals
        fx = fundamentals.usd_per_unit(info.get('financial_currency'))

        return {
            'income': _format_income(income, periods, inc_type, fx),
            'balance': _format_balance(balance, periods, bal_type, fx),
            'cashflow': _format_cashflow(cashflow, periods, cf_type, fx),
            'info': info,
        }
    except Exception as e:
        print(f"Yahoo finance error for {ticker}: {e}")
        return {'income': [], 'balance': [], 'cashflow': [], 'info': {}}


def _pick(preferred, fallback, ptype):
    """Return (df, type_label) — preferred frame if non-empty, else fallback."""
    if preferred is not None and not preferred.empty:
        return preferred, ptype
    alt_type = 'FY' if ptype == 'Q' else 'Q'
    if fallback is not None and not fallback.empty:
        return fallback, alt_type
    return preferred, ptype  # both empty -> preferred (empty) with requested label


def _get_info(t):
    """Minimal info needed for valuation metrics (price, shares, market cap)."""
    info = {}
    try:
        i = t.info
        info = {
            'name': i.get('shortName', ''),
            'price': i.get('currentPrice') or i.get('regularMarketPrice'),
            'market_cap': i.get('marketCap'),
            'shares_outstanding': i.get('sharesOutstanding'),
            'financial_currency': i.get('financialCurrency'),
        }
    except Exception as e:
        print(f"Yahoo info error: {e}")
    return info


def _c(v, fx):
    """Convert a statement value to USD (no-op when fx == 1.0).

    pandas rows return NaN for missing items — normalize to None so the
    payload never carries bare NaN (breaks browser JSON.parse).
    """
    if v is None or v != v:  # None or NaN
        return None
    if fx == 1.0:
        return v
    try:
        return v * fx
    except Exception:
        return v


def _grow(row, *keys):
    """First non-None value among keys in a pandas row (NaN -> None)."""
    for k in keys:
        v = row.get(k)
        if v is not None and v == v:  # NaN != NaN
            return v
    return None


def _format_income(df, periods, ptype, fx=1.0):
    """Format income statement"""
    if df is None or df.empty:
        return []

    # Transpose so periods are rows
    data = df.T.reset_index()
    data.columns = ['period'] + list(df.index)

    # Map to the canonical schema
    result = []
    for _, row in data.head(periods).iterrows():
        period = str(row['period'])[:10]
        result.append({
            'period': period,
            'type': ptype,
            'revenue': _c(row.get('Total Revenue'), fx),
            'cost_of_revenue': _c(row.get('Cost of Revenue'), fx),
            'gross_profit': _c(row.get('Gross Profit'), fx),
            'operating_income': _c(row.get('Operating Income'), fx),
            'net_income': _c(row.get('Net Income'), fx),
            'eps_basic': _c(row.get('Basic EPS'), fx),
            'eps_diluted': _c(row.get('Diluted EPS'), fx),
            'source': 'yahoo'
        })
    return result


def _format_balance(df, periods, ptype, fx=1.0):
    """Format balance sheet"""
    if df is None or df.empty:
        return []

    data = df.T.reset_index()
    data.columns = ['period'] + list(df.index)

    # Map common fields (canonical keys — term_debt kept as alias).
    # Foreign issuers name equity differently: no 'Total Stockholder Equity',
    # but 'Common Stock Equity' / 'Total Equity Gross Minority Interest'.
    result = []
    for _, row in data.head(periods).iterrows():
        period = str(row['period'])[:10]
        result.append({
            'period': period,
            'type': ptype,
            'cash': _c(row.get('Cash And Cash Equivalents'), fx),
            'net_receivables': _c(row.get('Net Receivables'), fx),
            'accounts_receivable': _c(row.get('Net Receivables'), fx),
            'marketable_securities': _c(row.get('Marketable Securities'), fx),
            'inventory': _c(row.get('Inventory'), fx),
            'ppe': _c(row.get('Net PPE'), fx),
            'total_assets': _c(row.get('Total Assets'), fx),
            'current_assets': _c(row.get('Current Assets'), fx),
            'total_liabilities': _c(row.get('Total Liabilities'), fx),
            'current_liabilities': _c(row.get('Current Liabilities'), fx),
            'short_term_debt': _c(row.get('Short Term Debt'), fx),
            'long_term_debt': _c(row.get('Long Term Debt'), fx),
            'term_debt': _c(row.get('Long Term Debt'), fx),
            'total_equity': _c(_grow(row, 'Total Stockholder Equity',
                                     'Common Stock Equity',
                                     'Total Equity Gross Minority Interest',
                                     'Stockholders Equity'), fx),
            'shares_outstanding': _grow(row, 'Ordinary Shares Number',
                                        'Share Issued'),
            'source': 'yahoo'
        })
    return result


def _format_cashflow(df, periods, ptype, fx=1.0):
    """Format cash flow statement"""
    if df is None or df.empty:
        return []

    data = df.T.reset_index()
    data.columns = ['period'] + list(df.index)

    result = []
    for _, row in data.head(periods).iterrows():
        period = str(row['period'])[:10]
        result.append({
            'period': period,
            'type': ptype,
            'operating_cf': _c(row.get('Operating Cash Flow'), fx),
            'capital_expenditure': _c(row.get('Capital Expenditure'), fx),
            'capex': _c(row.get('Capital Expenditure'), fx),
            'free_cf': _c(row.get('Free Cash Flow'), fx),
            'depreciation': _c(row.get('Depreciation Amortization Depletion'), fx),
            'stock_compensation': _c(row.get('Stock Based Compensation'), fx),
            'source': 'yahoo'
        })
    return result
