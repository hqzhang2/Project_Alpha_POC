"""
fundamentals_history.py — Point-in-time annual fundamentals store (SEC XBRL).

Phase 2.0 of the fundamentals-framework work: a filed-date-stamped SQLite
store of annual (10-K) fundamentals, so walk-forward studies can answer
"what did the books look like at rebalance date R" without look-ahead bias
(only facts filed <= R are visible).

  - Universe: S&P 500 constituents (Wikipedia, cached to data/) or an
    explicit --tickers list; CIKs via SEC company_tickers.json (cached).
  - Extraction: SEC companyfacts per CIK -> annual (10-K, ~365-day) facts,
    one row per (ticker, period_end) with the 10-K `filed` date.
  - Point-in-time query: get_snapshot(ticker, as_of) returns the latest
    annual row with filed <= as_of. Restatement granularity is the row's
    filing date (per-metric restatements within a filing are not tracked —
    documented v1 limitation).
  - Incremental: existing (ticker, period_end) rows are skipped unless
    --force. Resumable; the full S&P build is a long-running job.

Run:
  python3 fundamentals_history.py --limit 10              # first 10 SP500
  python3 fundamentals_history.py --tickers AAPL,MSFT     # explicit list
  python3 fundamentals_history.py --force --limit 5       # rebuild first 5
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime

import requests

HEADERS = {"User-Agent": "AlphaTerminal/1.0 research@example.com"}
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DATA_DIR, "fundamentals_hist.db")
SP500_CACHE = os.path.join(DATA_DIR, "sp500.json")
_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# Annual (10-K) US-GAAP tag map: metric -> [candidate XBRL tags].
# Flow items are 10-K ~365-day durations; balance/instant items are the
# instant facts at the fiscal-year-end balance date.
ANNUAL_TAGS = {
    'revenue': ['RevenueFromContractWithCustomerExcludingAssessedTax',
                'Revenues', 'SalesRevenueNet'],
    'gross_profit': ['GrossProfit'],
    'operating_income': ['OperatingIncomeLoss'],
    'net_income': ['NetIncomeLoss'],
    'eps_diluted': ['EarningsPerShareDiluted'],
    'current_assets': ['AssetsCurrent'],
    'current_liabilities': ['LiabilitiesCurrent'],
    'total_liabilities': ['Liabilities'],
    'short_term_debt': ['ShortTermBorrowings', 'CommercialPaper',
                        'LongTermDebtCurrent'],
    'long_term_debt': ['LongTermDebtNoncurrent'],
    'total_equity': ['StockholdersEquity'],
    'shares_outstanding': ['CommonStockSharesOutstanding'],
    'cash': ['CashAndCashEquivalentsAtCarryingValue'],
    'marketable_securities': ['MarketableSecuritiesCurrent'],
    'ppe': ['PropertyPlantAndEquipmentNet'],
    'operating_cf': ['NetCashProvidedByUsedInOperatingActivities'],
    'capex': ['PaymentsToAcquirePropertyPlantAndEquipment'],
}

# IFRS (ifrs-full) tag map — TSM/BHP (20-F foreign private issuers) file
# under IFRS. Same metric keys; first-found-wins after us-gaap.
IFRS_ANNUAL_TAGS = {
    'revenue': ['Revenue', 'RevenueFromContractsWithCustomers'],
    'gross_profit': ['GrossProfit'],
    'operating_income': ['ProfitLossFromOperatingActivities'],
    'net_income': ['ProfitLossAttributableToOwnersOfParent', 'ProfitLoss'],
    'eps_diluted': ['DilutedEarningsLossPerShare', 'BasicEarningsLossPerShare'],
    'current_assets': ['CurrentAssets'],
    'current_liabilities': ['CurrentLiabilities'],
    'total_liabilities': ['Liabilities'],
    'short_term_debt': ['ShorttermBorrowings', 'CurrentPortionOfLongtermBorrowings',
                        'OtherCurrentBorrowingsAndCurrentPortionOfOtherNoncurrentBorrowings'],
    'long_term_debt': ['LongtermBorrowings', 'NoncurrentPortionOfOtherNoncurrentBorrowings',
                       'Borrowings', 'OtherBorrowings'],
    'total_equity': ['EquityAttributableToOwnersOfParent', 'Equity'],
    'shares_outstanding': ['AdjustedWeightedAverageShares',
                           'WeightedAverageNumberOfOrdinarySharesOutstanding'],
    'cash': ['CashAndCashEquivalents'],
    'marketable_securities': [],
    'ppe': ['PropertyPlantAndEquipment'],
    'operating_cf': ['CashFlowsFromUsedInOperatingActivities'],
    'capex': ['AdditionsOtherThanThroughBusinessCombinationsPropertyPlantAndEquipment',
              'PurchaseOfPropertyPlantAndEquipment',
              'PaymentsForPropertyPlantAndEquipment'],
}

FLOW_KEYS = {'revenue', 'gross_profit', 'operating_income', 'net_income',
             'eps_diluted', 'operating_cf', 'capex'}   # ~365-day durations
INSTANT_KEYS = set(ANNUAL_TAGS) - FLOW_KEYS              # balance date instants


def _conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.execute("""
        CREATE TABLE IF NOT EXISTS annual (
            ticker TEXT, cik TEXT, period_end TEXT, filed TEXT,
            revenue REAL, gross_profit REAL, operating_income REAL,
            net_income REAL, eps_diluted REAL, current_assets REAL,
            current_liabilities REAL, total_liabilities REAL,
            short_term_debt REAL, long_term_debt REAL, total_equity REAL,
            shares_outstanding REAL, cash REAL, marketable_securities REAL,
            ppe REAL, operating_cf REAL, capex REAL,
            PRIMARY KEY (ticker, period_end)
        )""")
    c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("""CREATE TABLE IF NOT EXISTS prices (
        ticker TEXT, date TEXT, close REAL, PRIMARY KEY (ticker, date))""")
    c.commit()
    return c


# --------------------------------------------------------------------------- #
# Universe + CIK resolution
# --------------------------------------------------------------------------- #
def sp500_tickers():
    """S&P 500 constituents (current list — survivorship bias documented)."""
    if os.path.exists(SP500_CACHE):
        with open(SP500_CACHE) as f:
            return json.load(f)
    try:
        import io
        import pandas as pd
        # Wikipedia 403s on the default urllib UA — fetch with our headers first
        html = requests.get(_SP500_URL, headers=HEADERS, timeout=30).text
        tables = pd.read_html(io.StringIO(html))
        syms = [str(s).upper().replace(".", "-")
                for s in tables[0]["Symbol"].tolist()]
        with open(SP500_CACHE, "w") as f:
            json.dump(syms, f)
        return syms
    except Exception as e:
        print(f"sp500 fetch failed: {e}")
        return []


def resolve_ciks(tickers):
    """{TICKER: CIK} via SEC company_tickers.json (cached by sec_financials)."""
    import sec_financials
    tm = sec_financials._load_ticker_map()
    return {t.upper(): tm.get(t.upper()) for t in tickers}


# --------------------------------------------------------------------------- #
# Extraction (per ticker, from companyfacts)
# --------------------------------------------------------------------------- #
def _fetch_companyfacts(cik):
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik.zfill(10)}.json"
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    return r.json()


def _annual_facts(us_gaap, tag):
    """[(end, filed, val)] for one tag's 10-K annual facts.

    Flow items: ~365-day durations (10-K). Instant items: balance-date
    instants. Multiple versions of the same end (restatements / comparative
    columns in later 10-Ks) keep the EARLIEST filed — the as-originally-
    reported value. (Latest-filed would break point-in-time: every 10-K
    restates prior-year comparatives, so 'latest' for FY2024 lands on the
    FY2025 10-K filed a year later, making FY2024 look unavailable at
    rebalance dates in between. Verified live on AAPL, 2026-08.)
    """
    if tag not in us_gaap:
        return []
    units = us_gaap[tag].get("units", {})
    out = {}
    # Prefer USD-denominated units (SEC provides USD conversions for foreign
    # issuers: TSM Revenue has TWD+USD, EPS has TWD/shares+USD/shares) —
    # otherwise the first-listed unit (often home currency) wins ties.
    unit_keys = sorted(units.keys(), key=lambda k: 0 if "USD" in k else 1)
    for unit_facts in [units[k] for k in unit_keys]:
        for u in unit_facts:
            form = u.get("form", "")
            if "10-K" not in form and "20-F" not in form:
                continue
            end = u.get("end")
            if not end:
                continue
            start = u.get("start")
            if start:
                try:
                    days = (datetime.strptime(end, "%Y-%m-%d")
                            - datetime.strptime(start, "%Y-%m-%d")).days
                except ValueError:
                    continue
                if not (350 <= days <= 380):   # flow: ~365-day 10-K
                    continue
            filed = u.get("filed", end)
            # as-originally-reported: earliest filed version wins
            if end not in out or filed < out[end][0]:
                out[end] = (filed, u.get("val"))
    return [(end, filed, val) for end, (filed, val) in sorted(out.items())]


def extract_ticker(cik):
    """{period_end: {'filed': ..., metric: val}} from companyfacts (annual).

    us-gaap first, ifrs-full fallback (TSM/BHP are 20-F IFRS filers).
    First-found tag wins per metric; first-found taxonomy wins per period.
    """
    facts = _fetch_companyfacts(cik)
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    ifrs = facts.get("facts", {}).get("ifrs-full", {})
    rows = {}
    for taxo, tag_map in ((us_gaap, ANNUAL_TAGS), (ifrs, IFRS_ANNUAL_TAGS)):
        for metric, tags in tag_map.items():
            for tag in tags:
                for end, filed, val in _annual_facts(taxo, tag):
                    row = rows.setdefault(end, {"filed": filed})
                    if filed > row.get("filed", ""):
                        row["filed"] = filed
                    if metric not in row:
                        row[metric] = val
    return rows


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #
def store_ticker(ticker, cik, force=False):
    conn = _conn()
    try:
        if not force:
            cur = conn.execute("SELECT COUNT(*) FROM annual WHERE ticker = ?",
                               (ticker,))
            if cur.fetchone()[0] > 0:
                return 0, "cached"
        rows = extract_ticker(cik)
        if not rows:
            return 0, "no-data"
        for end, m in rows.items():
            conn.execute(
                """INSERT OR REPLACE INTO annual
                   (ticker, cik, period_end, filed, revenue, gross_profit,
                    operating_income, net_income, eps_diluted, current_assets,
                    current_liabilities, total_liabilities, short_term_debt,
                    long_term_debt, total_equity, shares_outstanding, cash,
                    marketable_securities, ppe, operating_cf, capex)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ticker, cik, end, m.get("filed"),
                 m.get("revenue"), m.get("gross_profit"),
                 m.get("operating_income"), m.get("net_income"),
                 m.get("eps_diluted"), m.get("current_assets"),
                 m.get("current_liabilities"), m.get("total_liabilities"),
                 m.get("short_term_debt"), m.get("long_term_debt"),
                 m.get("total_equity"), m.get("shares_outstanding"),
                 m.get("cash"), m.get("marketable_securities"), m.get("ppe"),
                 m.get("operating_cf"), m.get("capex")))
        conn.commit()
        return len(rows), "stored"
    finally:
        conn.close()


def get_snapshot(ticker, as_of):
    """Latest annual row with filed <= as_of (point-in-time). None if none."""
    conn = _conn()
    try:
        cur = conn.execute(
            """SELECT * FROM annual WHERE ticker = ? AND filed <= ?
               ORDER BY period_end DESC LIMIT 1""", (ticker, as_of))
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    finally:
        conn.close()


def history(ticker):
    """All annual rows for a ticker, oldest first."""
    conn = _conn()
    try:
        cur = conn.execute(
            "SELECT * FROM annual WHERE ticker = ? ORDER BY period_end",
            (ticker,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Prices (daily closes, for EV / P-E at rebalance dates)
# --------------------------------------------------------------------------- #
def store_prices(ticker, closes):
    """closes: {date_str: close} — upserted into the prices table."""
    conn = _conn()
    try:
        conn.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?)",
                         [(ticker, d, float(c)) for d, c in closes.items()])
        conn.commit()
    finally:
        conn.close()


def price_on(ticker, date):
    """Last close on/before date. None when no data."""
    conn = _conn()
    try:
        cur = conn.execute(
            """SELECT close FROM prices WHERE ticker = ? AND date <= ?
               ORDER BY date DESC LIMIT 1""", (ticker, date))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def has_prices(ticker):
    conn = _conn()
    try:
        cur = conn.execute("SELECT COUNT(*) FROM prices WHERE ticker = ?",
                           (ticker,))
        return cur.fetchone()[0] > 0
    finally:
        conn.close()


def fetch_prices(ticker, start="2014-01-01"):
    """Daily closes via yfinance (auto_adjust), as {date_str: close}."""
    import yfinance as yf
    df = yf.Ticker(ticker).history(start=start, auto_adjust=True)
    return {d.strftime("%Y-%m-%d"): float(c)
            for d, c in df["Close"].dropna().items()}


def ensure_prices(ticker, start="2014-01-01"):
    """Fetch + store prices if not already present. Returns row count."""
    if has_prices(ticker):
        return 0
    closes = fetch_prices(ticker, start)
    if closes:
        store_prices(ticker, closes)
    return len(closes)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tickers", default="")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = sp500_tickers()
        if args.limit:
            tickers = tickers[:args.limit]
    if not tickers:
        print("no universe; try --tickers AAPL,MSFT")
        return

    ciks = resolve_ciks(tickers)
    missing = [t for t, c in ciks.items() if not c]
    if missing:
        print(f"no CIK for: {', '.join(missing[:10])}"
              f"{' …' if len(missing) > 10 else ''}")

    ok = cached = nodata = fail = 0
    t0 = time.time()
    for i, t in enumerate(tickers, 1):
        cik = ciks.get(t)
        if not cik:
            fail += 1
            continue
        try:
            n, status = store_ticker(t, cik, force=args.force)
            if status == "stored":
                ok += 1
                print(f"[{i}/{len(tickers)}] {t}: {n} annual rows")
            elif status == "cached":
                cached += 1
            else:
                nodata += 1
        except Exception as e:
            fail += 1
            print(f"[{i}/{len(tickers)}] {t}: FAILED {e}")
        time.sleep(0.15)  # SEC politeness
    print(f"done: {ok} stored, {cached} cached, {nodata} no-data, "
          f"{fail} failed in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    sys.exit(main())
