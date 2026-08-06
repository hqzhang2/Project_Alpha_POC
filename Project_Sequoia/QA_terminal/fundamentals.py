"""
fundamentals.py — Shared fundamental-analysis metrics (Graham / Intelligent Investor).

Single source of truth for valuation metrics across data providers. Both
sec_financials.py (SEC EDGAR XBRL) and yahoo_financials.py (Yahoo fallback
for ADRs / foreign issuers) normalize their statement rows and call
calculate_graham_metrics() here — one scorecard, no provider drift.

Normalized row keys (aliases tolerated via _g()):
  income:   period, type ('Q'|'FY'), revenue, gross_profit, operating_income,
            net_income, eps_basic, eps_diluted, diluted_shares
  balance:  period, type, cash, net_receivables, inventory, current_assets,
            total_assets, current_liabilities, total_liabilities,
            short_term_debt, long_term_debt, total_equity, shares_outstanding
  cashflow: period, type, operating_cf, capex, free_cf, dividends
  stock_info: price, market_cap, shares_outstanding  (used for ADR-ratio
            normalization of per-share metrics)

Graham-specific definitions used here (the "correct" readings):
  - D/E is (short-term + long-term debt) / equity — NOT total liabilities /
    equity. Total-liabilities D/E penalizes companies for payables/accruals,
    which is not Graham's criterion.
  - Graham Number uses TTM EPS (sum of last 4 quarters, or last FY when
    quarterly data is incomplete), never a single quarter.
  - Per-share metrics are scaled by the ADR ratio for foreign listings:
    R = price * shares_outstanding / market_cap. A US name derives R ≈ 1;
    TSM derives R ≈ 5 (1 ADR = 5 ordinary shares), BABA ≈ 8, BHP ≈ 2.
    EPS/BVPS per ADR = per-ordinary-share * R.
  - Earnings yield is TTM EPS / price (Graham compared this to bond yields).
"""

import time

# --- FX cache: Yahoo 'XXX=X' quotes are UNITS-PER-USD (CNY=X 6.75 = 6.75
# CNY per 1 USD). usd_per_unit returns USD per 1 unit (1/6.75 = 0.148). -----
_FX_CACHE = {}
_FX_TTL = 3600


def usd_per_unit(currency):
    """USD per 1 unit of `currency`. Cached 1h. 1.0 for USD/unknown (fail-open).

    Providers normalize foreign-issuer statements (BABA=CNY, TSM=TWD) to USD
    with this rate so the metrics layer can assume USD-consistent inputs.
    """
    if not currency or str(currency).upper() == 'USD':
        return 1.0
    cur = str(currency).upper()
    now = time.time()
    hit = _FX_CACHE.get(cur)
    if hit and now - hit[1] < _FX_TTL:
        return hit[0]
    try:
        import yfinance as yf
        i = yf.Ticker(f'{cur}=X').info
        rate = i.get('regularMarketPrice') or i.get('previousClose')
        if rate and float(rate) > 0:
            usd_per = 1.0 / float(rate)
            _FX_CACHE[cur] = (usd_per, now)
            return usd_per
    except Exception as e:
        print(f'FX lookup failed for {cur}: {e}')
    return 1.0


def _g(row, *keys):
    """First non-None value among keys (alias-tolerant row access)."""
    if not row:
        return None
    for k in keys:
        v = row.get(k)
        if v is not None:
            return v
    return None


def _num(v):
    """Safe float conversion; None/NaN -> None."""
    if v is None:
        return None
    try:
        import pandas as pd
        if pd.isna(v):
            return None
    except Exception:
        pass
    try:
        return float(v)
    except Exception:
        return None


def _zero(v):
    x = _num(v)
    return x if x is not None else 0.0


def ttm_eps(income):
    """TTM diluted EPS.

    Quarterly view: sum the last 4 'Q' rows (consecutive available quarters).
    If fewer than 4 quarterly rows exist, fall back to the latest 'FY' row.
    Returns (eps_ttm, source_desc) — source_desc is 'Q' or 'FY'.
    """
    if not income:
        return 0.0, 'NONE'
    q_rows = [r for r in income if r.get('type') == 'Q']
    if len(q_rows) >= 4:
        eps = sum(_zero(_g(r, 'eps_diluted', 'eps')) for r in q_rows[:4])
        return eps, 'Q'
    fy_rows = [r for r in income if r.get('type') == 'FY']
    if fy_rows:
        return _zero(_g(fy_rows[0], 'eps_diluted', 'eps')), 'FY'
    # No typed rows — treat the leading rows as quarterly by position.
    eps = sum(_zero(_g(r, 'eps_diluted', 'eps')) for r in income[:4])
    return eps, 'POS'


# Curated ADR ratios: 1 ADR/ADS = N ordinary shares (prospectus-verified).
# Only multi-share ratios need entries; default 1.0 is correct for US names
# and 1:1 ADRs. Extend as new foreign listings are analyzed — verify each
# against the deposit agreement before adding.
ADR_RATIOS = {
    'TSM': 5,    # TSMC: 1 ADR = 5 ordinary shares (TWSE 2330)
    'BABA': 8,   # Alibaba: 1 ADS = 8 ordinary shares
    'BHP': 2,    # BHP Group: 1 ADR = 2 ordinary shares
    'JD': 2,     # JD.com: 1 ADS = 2 Class A ordinary shares
    'NTES': 5,   # NetEase: 1 ADS = 5 ordinary shares
    'PBR': 2,    # Petrobras: 1 ADR = 2 preferred shares
}


def derive_adr_ratio(ticker, stock_info=None):
    """ADR ratio R = number of ordinary shares per ADR/ADS.

    LIVE-VERIFIED 2026-08: there is NO reliable ratio signal in yfinance
    `info` — `marketCap` is computed as `price * sharesOutstanding` internally
    (R == 1.000 for every ticker by construction), and `bookValue` is
    inconsistent garbage for foreign listings (ASML=1, SAP=3 vs real equity).
    So this uses a curated, prospectus-verified table. Default 1.0 is correct
    for all US names and 1:1 ADRs (SAP, ASML, NIO, VALE, HMC...) — only
    multi-share ADRs need entries here.
    """
    r = ADR_RATIOS.get((ticker or '').upper())
    return r if r else 1.0


def calculate_graham_metrics(income, balance, cashflow, stock_info=None,
                             ticker=None):
    """Graham / Intelligent Investor scorecard on normalized statement rows.

    Returns a dict with per-share metrics (ADR-ratio adjusted), the
    0-12 valuation score, and the star rating. Empty when no usable data.
    """
    m = {}
    if not income or not balance:
        return m

    stock_info = stock_info or {}
    price = _num(stock_info.get('price'))
    if stock_info.get('shares_outstanding') is not None:
        shares = _num(stock_info.get('shares_outstanding'))
    else:
        shares = _num(_g(balance[0], 'shares_outstanding', 'diluted_shares'))
    adr_ratio = derive_adr_ratio(ticker, stock_info)
    m['adr_ratio'] = round(adr_ratio, 4)

    inc = income[0]
    bs = balance[0]

    # --- Balance-sheet health (correct Graham definitions) ---
    ca = _zero(_g(bs, 'current_assets'))
    cl = _zero(_g(bs, 'current_liabilities'))
    cash = _zero(_g(bs, 'cash'))
    receivables = _zero(_g(bs, 'net_receivables', 'accounts_receivable'))
    equity = _zero(_g(bs, 'total_equity'))
    st_debt = _zero(_g(bs, 'short_term_debt'))
    lt_debt = _zero(_g(bs, 'long_term_debt', 'term_debt'))
    total_liab = _zero(_g(bs, 'total_liabilities'))

    m['current_ratio'] = round(ca / cl, 2) if cl else None
    m['quick_ratio'] = round((cash + receivables) / cl, 2) if cl else None
    m['debt_to_equity'] = round((st_debt + lt_debt) / equity, 2) if equity else None
    m['ncav'] = round(ca - total_liab, 2)

    # --- Per-share (ADR-ratio adjusted) ---
    eps_ttm, eps_src = ttm_eps(income)
    eps_ttm_adj = eps_ttm * adr_ratio if eps_ttm else 0.0
    bvps = equity / shares if shares else 0.0
    bvps_adj = bvps * adr_ratio if bvps else 0.0
    m['eps_ttm'] = round(eps_ttm_adj, 4)
    m['eps_source'] = eps_src
    m['bvps'] = round(bvps_adj, 4)

    if price and eps_ttm_adj > 0:
        m['pe_ratio'] = round(price / eps_ttm_adj, 2)
        m['earnings_yield'] = round(eps_ttm_adj / price * 100, 2)
    else:
        m['pe_ratio'] = None
        m['earnings_yield'] = None

    if eps_ttm_adj > 0 and bvps_adj > 0:
        m['graham_number'] = round((22.5 * eps_ttm_adj * bvps_adj) ** 0.5, 2)
        m['price_to_graham'] = round(price / m['graham_number'], 2) if price else None
    else:
        m['graham_number'] = None
        m['price_to_graham'] = None

    ncav_ps = m['ncav'] * adr_ratio / shares if shares else 0.0
    m['ncav_per_share'] = round(ncav_ps, 4)
    m['price_to_ncav'] = round(price / ncav_ps, 2) if price and ncav_ps > 0 else None

    # --- Margins ---
    rev = _zero(_g(inc, 'revenue'))
    gp = _zero(_g(inc, 'gross_profit'))
    ni = _zero(_g(inc, 'net_income'))
    if rev > 0:
        m['gross_margin'] = round(gp / rev * 100, 2)
        m['net_margin'] = round(ni / rev * 100, 2)

    # --- Graham score (0-12) ---
    score = 0
    cr = m.get('current_ratio')
    if cr is not None:
        if cr >= 2.0:
            score += 2
        elif cr >= 1.5:
            score += 1
    de = m.get('debt_to_equity')
    if de is not None:
        if de < 0.5:
            score += 2
        elif de < 1.0:
            score += 1
    pe = m.get('pe_ratio')
    if pe is not None and 0 < pe < 15:
        score += 2
    elif pe is not None and 15 <= pe < 20:
        score += 1
    ey = m.get('earnings_yield')
    if ey is not None and ey > 6.67:
        score += 2
    elif ey is not None and ey > 4.0:
        score += 1
    nm = m.get('net_margin')
    if nm is not None:
        if nm > 15:
            score += 2
        elif nm > 10:
            score += 1
    ptg = m.get('price_to_graham')
    if ptg is not None and 0 < ptg < 1.0:
        score += 2
    elif ptg is not None and 1.0 <= ptg < 1.5:
        score += 1

    m['valuation_score'] = score
    m['score'] = score
    if score >= 8:
        m['rating'] = '⭐⭐⭐ Strong Buy'
    elif score >= 6:
        m['rating'] = '⭐⭐ Buy'
    elif score >= 4:
        m['rating'] = '⭐ Hold'
    elif score >= 2:
        m['rating'] = '⚠️ Speculative'
    else:
        m['rating'] = '❌ Avoid'
    return m
