"""Deterministic collectors for the sentiment strip (Phase 2).

  oi_store — put/call OI ratio per (date, ticker) from the terminal's own
             data/option_oi.db (fed by com.alpha.terminal.oi.snapshot.prod).
             value = put_oi/call_oi; sentiment = call_share normalization.
             Incremental: only dates newer than the last stored reading.
  breadth  — NYSE A/D breadth from Yahoo ^NYAD daily change.
             value = net advancers-decliners (index change); sentiment =
             trailing-252d percentile (higher net = more bullish).

Both write readings via sentiment_db.upsert_reading (idempotent natural key).
Fail-open: any source error -> 0 rows, never a crash.
Run: python sentiment_collect.py   (deterministic, stdout is the deliverable)

NOTE: `import sentiment` is deliberately LAZY (inside functions) — sentiment.py
imports this module at its bottom to auto-register providers; top-level import
here would create a circular import.
"""
import datetime
import logging
import os
import sqlite3

import sentiment_db as db

logger = logging.getLogger(__name__)

try:
    import yfinance as yf
except ImportError:
    yf = None  # fail-open: breadth collector skips when yfinance is unavailable

_OI_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "option_oi.db")

METRIC_OI = "put_call_oi_ratio"
SOURCE_OI = "oi_store"
METRIC_BREADTH = "breadth_ad"
SOURCE_BREADTH = "breadth"


def compute_oi_ratio(date, ticker):
    """Compute + upsert ONE (date, ticker) put/call OI ratio from option_oi.db.

    On-demand path (dashboard watchlist add): after snapshot_ticker stores the
    chain, this derives the ratio reading so the chart overlay + chips get
    data immediately. Returns 1 on write, 0 on no-data/fail-open.
    """
    import sentiment  # lazy
    if not os.path.exists(_OI_DB):
        return 0
    try:
        conn = sqlite3.connect(_OI_DB)
        try:
            row = conn.execute(
                "SELECT "
                "  SUM(CASE WHEN type='Call' THEN oi ELSE 0 END) AS call_oi, "
                "  SUM(CASE WHEN type='Put' THEN oi ELSE 0 END) AS put_oi "
                "FROM contract_oi WHERE date=? AND ticker=?",
                (date, ticker),
            ).fetchone()
        finally:
            conn.close()
        if not row or not row[0] or not row[1]:
            return 0
        ratio = row[1] / row[0]
        sent = sentiment.normalize(ratio, "call_share")
        return 1 if db.upsert_reading(date, "ticker", ticker, METRIC_OI, SOURCE_OI,
                                      ratio, sent, count=int(row[0] + row[1])) else 0
    except Exception as e:
        logger.error("oi ratio %s %s failed: %s", date, ticker, e)
        return 0


def collect_oi_ratios():
    """Put/call OI ratio per (date, ticker) from option_oi.db. Incremental. Returns rows written."""
    import sentiment  # lazy: avoids circular import

    if not os.path.exists(_OI_DB):
        logger.warning("option_oi.db missing — oi_store collector skipped")
        return 0
    latest = db.latest_reading_date_for(METRIC_OI, SOURCE_OI)
    conn = sqlite3.connect(_OI_DB)
    try:
        rows = conn.execute(
            "SELECT date, ticker, "
            "  SUM(CASE WHEN type='Call' THEN oi ELSE 0 END) AS call_oi, "
            "  SUM(CASE WHEN type='Put' THEN oi ELSE 0 END) AS put_oi "
            "FROM contract_oi "
            "WHERE date > COALESCE(?, '') "
            "GROUP BY date, ticker "
            "HAVING call_oi > 0 AND put_oi > 0 "
            "ORDER BY date, ticker",
            (latest or "",),
        ).fetchall()
    finally:
        conn.close()

    n = 0
    for date, ticker, call_oi, put_oi in rows:
        ratio = put_oi / call_oi
        sent = sentiment.normalize(ratio, "call_share")
        if db.upsert_reading(date, "ticker", ticker, METRIC_OI, SOURCE_OI,
                             ratio, sent, count=int(call_oi + put_oi)):
            n += 1
    return n


# Fixed liquid breadth universe: MAG7 + large caps + sector/theme ETFs.
# Deterministic, boring-feed (Yahoo daily closes). NOTE: Yahoo delisted its
# NYSE-wide A/D indices (^NYAD/^NAH/^NAL, 404 "Quote not found") — this 39-name
# proxy was approved by Hong 2026-08-07 as the replacement (documented proxy).
BREADTH_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "JPM", "V",
    "UNH", "XOM", "PG", "MA", "HD", "COST", "LLY", "NFLX", "CRM", "AMD", "MU",
    "GS", "SPY", "QQQ", "IWM", "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLB",
    "XLRE", "XLC", "XLU", "SMH", "IBIT", "GLD", "TLT",
]


def collect_breadth():
    """A/D breadth across BREADTH_UNIVERSE from Yahoo daily closes.

    value = (advancers - decliners) / (advancers + decliners), i.e. a -1..+1
    share -> sentiment = passthrough. Returns rows written (0 or 1). Fail-open.
    """
    import sentiment  # lazy

    if yf is None:
        logger.warning("yfinance unavailable — breadth collector skipped")
        return 0
    try:
        df = yf.download(BREADTH_UNIVERSE, period="2d", interval="1d",
                         progress=False, auto_adjust=False)
    except Exception as e:
        logger.error("breadth fetch failed: %s", e)
        return 0
    if df is None or df.empty or "Close" not in df:
        return 0
    closes = df["Close"].dropna(axis=1)
    if len(closes) < 2 or closes.shape[1] == 0:
        return 0
    try:
        chg = closes.iloc[-1] - closes.iloc[-2]
    except Exception as e:
        logger.error("breadth computation failed: %s", e)
        return 0
    adv = int((chg > 0).sum())
    dec = int((chg < 0).sum())
    if adv + dec == 0:
        return 0
    share = (adv - dec) / (adv + dec)
    asof = str(closes.index[-1])[:10]
    sent = sentiment.normalize(share, "passthrough")
    return 1 if db.upsert_reading(asof, "market", None, METRIC_BREADTH, SOURCE_BREADTH,
                                  share, sent, count=adv + dec) else 0


def register_all():
    """Register collectors with the sentiment provider registry."""
    import sentiment
    sentiment.register_provider(SOURCE_OI, collect_oi_ratios)
    sentiment.register_provider(SOURCE_BREADTH, collect_breadth)
    sentiment.register_provider(SOURCE_CBOE, collect_cboe)
    sentiment.register_provider(SOURCE_FINRA, collect_finra_short_interest)
    sentiment.register_provider(SOURCE_NAAIM, collect_naaim)
    sentiment.register_provider(SOURCE_AV, collect_av_news)
    sentiment.register_provider(SOURCE_AAII, collect_aaii)
    sentiment.register_provider(SOURCE_COT, collect_cot)
    sentiment.register_provider(SOURCE_MARGIN, collect_margin_debt)
    sentiment.register_provider(SOURCE_EDGAR, collect_edgar)
    sentiment.register_provider(SOURCE_STOCKTWITS, collect_stocktwits)


# ---------------------------------------------------------------------------
# Next release: COT (CFTC) + Margin Debt (FINRA) — both free, keyless.
#
# COT: CFTC split equity-index futures into the FINANCIAL futures report
#   (`fut_fin_txt_YYYY.zip` -> FinFutYY.txt). Market "E-MINI S&P 500 - CHICAGO
#   MERCANTILE EXCHANGE". Net spec = Asset_Mgr + Lev_Money (long - short).
#   Weekly, ~3-4 day lag. (The regular fut_disagg/com_disagg files contain NO
#   financial futures — live-verified 2026-08-07.)
# Margin: FINRA margin-statistics page embeds the monthly table server-side
#   (Debit Balances in Customers' Securities Margin Accounts, $M). Monthly,
#   ~6 week lag. Source name finra_margin (distinct from finra short interest).
# ---------------------------------------------------------------------------

SOURCE_COT = "cot"
SOURCE_MARGIN = "finra_margin"

COT_FIN_URL = "https://www.cftc.gov/files/dea/history/fut_fin_txt_{year}.zip"
COT_MARKET = "E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE"
MARGIN_URL = "https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics"


def collect_cot():
    """COT net spec positioning on E-mini S&P 500 futures (CFTC financial report).

    Download current-year zip, parse FinFutYY.txt, filter the E-mini S&P 500
    market, take the latest report date. value = net spec contracts
    (Asset_Mgr + Lev_Money longs - shorts); sentiment = percentile (higher =
    bullish). Fail-open: download/parse error -> 0.
    """
    import sentiment  # lazy
    import csv
    import io
    import zipfile

    year = datetime.date.today().year
    try:
        import requests
        r = requests.get(COT_FIN_URL.format(year=year), timeout=30)
        r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        name = z.namelist()[0]
        rows = list(csv.DictReader(io.StringIO(z.read(name).decode("utf-8", errors="ignore"))))
        sp = [row for row in rows if row.get("Market_and_Exchange_Names") == COT_MARKET]
        if not sp:
            logger.error("COT: market %r not found in %s", COT_MARKET, name)
            return 0
        latest = max(sp, key=lambda row: row["Report_Date_as_YYYY-MM-DD"])
        am_l = int(latest.get("Asset_Mgr_Positions_Long_All") or 0)
        am_s = int(latest.get("Asset_Mgr_Positions_Short_All") or 0)
        lm_l = int(latest.get("Lev_Money_Positions_Long_All") or 0)
        lm_s = int(latest.get("Lev_Money_Positions_Short_All") or 0)
        value = float((am_l + lm_l) - (am_s + lm_s))
        asof = latest["Report_Date_as_YYYY-MM-DD"]
        hist = db.history_for("cot_net_spec", limit=252)
        sent = sentiment.normalize(value, "percentile", hist) if hist else None
        return 1 if db.upsert_reading(asof, "market", None, "cot_net_spec", SOURCE_COT,
                                      value, sent, count=int(latest.get("Open_Interest_All") or 0)) else 0
    except Exception as e:
        logger.error("COT fetch failed: %s", e)
        return 0


def collect_margin_debt():
    """FINRA margin debt (monthly, $M) from the margin-statistics page table.

    Page embeds the monthly table server-side; first data row = latest month.
    value = Debit Balances in Customers' Securities Margin Accounts ($M);
    sentiment = percentile (higher = bullish — leverage appetite).
    Fail-open: fetch/parse error -> 0.
    """
    import sentiment  # lazy
    import re

    page = _fetch_text(MARGIN_URL)
    if not page:
        return 0
    m = re.search(r"<table.*?</table>", page, re.S)
    if not m:
        logger.error("MARGIN: table not found")
        return 0
    rows = re.findall(r"<tr.*?</tr>", m.group(0), re.S)
    if len(rows) < 2:
        return 0
    cells = [re.sub(r"<[^>]+>", "", c).strip()
             for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", rows[1], re.S)]
    if len(cells) < 2 or not cells[0] or not cells[1]:
        return 0
    mo, yy = cells[0].split("-")  # e.g. Jun-26
    asof = f"20{yy}-{datetime.datetime.strptime(mo, '%b').month:02d}-01"
    value = float(cells[1].replace(",", "")) / 1000.0  # source reports $M -> store $B
    hist = db.history_for("margin_debt", limit=252)
    sent = sentiment.normalize(value, "percentile", hist) if hist else None
    return 1 if db.upsert_reading(asof, "market", None, "margin_debt", SOURCE_MARGIN,
                                  value, sent, count=len(rows) - 1) else 0


# ---------------------------------------------------------------------------
# Per-ticker dashboard tiles: EDGAR insider + StockTwits social (2026-08-07).
#
# EDGAR: submissions API -> recent Form 4s within 90d -> per-filing
#   form4.xml -> net buy $M (P buys - S sells at market price). Detail rows
#   go to insider_filings for the drill-down modal. value = net $M (raw),
#   sentiment = percentile over market-cap-scaled (bps) history so the signal
#   is comparable across tickers (AAPL $12M vs microcap $12M are different
#   convictions). SEC requires a UA with contact info.
# StockTwits: stream API (browser UA — python-requests UA gets 403) paged via
#   cursor.max (NOT next) until 7d covered or page cap. Classification is
#   SPARSE (~20% of messages carry Bullish/Bearish); spread = (bull-bear)/
#   classified, count = total messages (volume context). Detail rows ->
#   social_daily for the modal.
# ---------------------------------------------------------------------------

SOURCE_EDGAR = "edgar"
SOURCE_STOCKTWITS = "stocktwits"

EDGAR_HEADERS = {"User-Agent": "alpha-terminal/1.0 (quant dashboard; chuck@example.com)"}
EDGAR_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
EDGAR_FILING = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{doc}"
EDGAR_WINDOW_DAYS = 90          # Hong-approved: 90d window
STOCKTWITS_URL = "https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
STOCKTWITS_WINDOW_DAYS = 7      # Hong-approved: 7d aggregate
STOCKTWITS_MAX_PAGES = 20       # ~600 msgs cap; count is a volume proxy
# StockTwits 403s the python-requests UA; browser UA required (verified live).
_STOCKTWITS_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


def _edgar_cik(ticker):
    """CIK for a ticker via the existing SEC map seam. None on miss."""
    try:
        import sec_edgar
        return sec_edgar.get_cik(ticker)
    except Exception:
        return None


def _edgar_fetch(url):
    import requests
    r = requests.get(url, headers=EDGAR_HEADERS, timeout=20)
    r.raise_for_status()
    return r


def _parse_form4(xml_text):
    """Form 4 XML -> list of transaction dicts (code, shares, price)."""
    import re
    rows = []
    # transaction blocks: code + shares + price (+ date)
    blocks = re.split(r"</nonDerivativeTransaction>", xml_text)
    for b in blocks:
        code_m = re.search(r"<transactionCode>(\w)</transactionCode>", b)
        sh_m = re.search(r"<transactionShares>\s*<value>([\d.]+)</value>", b)
        pr_m = re.search(r"<transactionPricePerShare>\s*<value>([\d.]+)</value>", b)
        dt_m = re.search(r"<transactionDate>\s*<value>(\d{4}-\d{2}-\d{2})</value>", b)
        if code_m and sh_m:
            try:
                rows.append({
                    "code": code_m.group(1),
                    "shares": float(sh_m.group(1)),
                    "price": float(pr_m.group(1)) if pr_m else None,
                    "date": dt_m.group(1) if dt_m else None,
                })
            except ValueError:
                continue
    return rows


def collect_edgar():
    """Per-ticker insider net buy from SEC EDGAR Form 4s (90d window).

    Iterates the watchlist (or a sane fallback), writes one reading per
    ticker + insider_filings detail rows. value = net $M (P-S); sentiment =
    percentile over bps-scaled history (value / market cap). Fail-open.
    """
    import sentiment  # lazy
    import os
    import sys

    tickers = _watchlist_tickers()
    if not tickers:
        return 0
    wrote = 0
    for t in tickers:
        try:
            wrote += _collect_edgar_one(t, sentiment)
        except Exception as e:
            logger.error("EDGAR %s failed: %s", t, e)
    return wrote


def _collect_edgar_one(ticker, sentiment):
    import json
    import os

    cik = _edgar_cik(ticker)
    if not cik:
        logger.warning("EDGAR: no CIK for %s", ticker)
        return 0
    cutoff = (datetime.date.today() - datetime.timedelta(days=EDGAR_WINDOW_DAYS)).isoformat()

    # 1. Recent filings -> Form 4s in window
    data = _edgar_fetch(EDGAR_SUBMISSIONS.format(cik=cik)).json()
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accs = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    dates = recent.get("filingDate", [])
    f4 = [(accs[i], docs[i], dates[i]) for i in range(len(forms))
          if forms[i] == "4" and dates[i] >= cutoff]
    if not f4:
        logger.info("EDGAR %s: no Form 4s in window", ticker)
        return 0

    # 2. Per-filing detail + net buy/sell
    detail, net = [], 0.0
    for acc, doc, fdate in f4:
        acc_flat = acc.replace("-", "")
        try:
            # primaryDocument like 'xslF345X06/form4.xml' = render path; the
            # real file is form4.xml at the accession root (verified live).
            doc_name = doc.split("/")[-1] if doc.startswith("xsl") else doc
            xml = _edgar_fetch(EDGAR_FILING.format(cik=cik, accession=acc_flat, doc=doc_name)).text
            txs = _parse_form4(xml)
            insider = _parse_form4_owner(xml)
            for tx in txs:
                code, shares, price = tx["code"], tx["shares"], tx.get("price")
                val = shares * price if price else 0.0
                signed = val if code == "P" else (-val if code == "S" else 0.0)
                net += signed
                detail.append({"filing_date": tx.get("date") or fdate, "insider": insider,
                               "role": None, "code": code, "shares": shares,
                               "price": price, "value": round(signed, 2)})
        except Exception as e:
            logger.warning("EDGAR %s filing %s failed: %s", ticker, acc, e)
            continue

    if not detail:
        return 0
    db.replace_filings(ticker, detail)

    # 3. Reading: raw net $M; percentile over bps history (market-cap scaled)
    value_m = round(net / 1e6, 4)  # $M
    bps = _insider_bps(ticker, net)
    hist = db.history_for("insider_net_buy", limit=252)
    hist_bps = [_insider_bps(ticker, v * 1e6) for v in hist] if hist else []
    sent = sentiment.normalize(bps, "percentile", hist_bps) if hist_bps else None
    asof = datetime.date.today().isoformat()
    return 1 if db.upsert_reading(asof, "ticker", ticker, "insider_net_buy", SOURCE_EDGAR,
                                  value_m, sent, count=len(detail)) else 0


def _parse_form4_owner(xml_text):
    """Reporting owner name from Form 4 XML (or None)."""
    import re
    # rptOwnerCik precedes rptOwnerName inside reportingOwnerId
    m = re.search(r"<rptOwnerName>([^<]+)</rptOwnerName>", xml_text)
    return m.group(1).strip() if m else None


def _insider_bps(ticker, net_usd):
    """Net insider $ as basis points of market cap (bps = $ / cap * 1e4).

    Uses the live market cap via the quotes seam; None on any miss (the
    percentile history then just skips this point).
    """
    try:
        import quotes
        q = quotes.get_quote(ticker)
        cap = (q or {}).get("market_cap")
        if not cap:
            return None
        return net_usd / cap * 1e4
    except Exception:
        return None


def _watchlist_tickers():
    """Watchlist tickers (localStorage-backed) or the dashboard default set."""
    try:
        import json
        import os
        p = os.path.expanduser("~/Library/Application Support/AlphaTerminal/watchlist.json")
        if os.path.exists(p):
            with open(p) as f:
                wl = json.load(f)
            if wl:
                return [t.upper() for t in wl]
    except Exception:
        pass
    return ["AAPL", "AMZN", "GLD", "GOOGL", "MSFT", "NVDA", "QQQ", "SMH", "SPY", "TSLA", "XLE"]


def collect_stocktwits():
    """Per-ticker StockTwits bull-bear spread over a 7d window.

    Stream API paged via cursor.max (NOT next). Classification is sparse;
    spread = (bull-bear)/classified over the window, count = total messages.
    Detail rows -> social_daily. Fail-open.
    """
    import sentiment  # lazy

    tickers = _watchlist_tickers()
    if not tickers:
        return 0
    wrote = 0
    for t in tickers:
        try:
            wrote += _collect_stocktwits_one(t, sentiment)
        except Exception as e:
            logger.error("STOCKTWITS %s failed: %s", t, e)
    return wrote


def _collect_stocktwits_one(ticker, sentiment):
    import requests
    from collections import defaultdict

    headers = _STOCKTWITS_HEADERS
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=STOCKTWITS_WINDOW_DAYS)
    cursor_max = None
    by_day = defaultdict(lambda: {"messages": 0, "classified": 0, "bull": 0, "bear": 0})
    bull = bear = total_msgs = 0
    pages = 0
    while pages < STOCKTWITS_MAX_PAGES:
        url = STOCKTWITS_URL.format(ticker=ticker)
        if cursor_max:
            url += f"?max={cursor_max}"
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
        msgs = data.get("messages", [])
        if not msgs:
            break
        for m in msgs:
            created = m.get("created_at", "")
            try:
                dt = datetime.datetime.strptime(created[:19], "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                continue
            if dt < cutoff:
                break  # passed the window; stop (newest-first)
            day = dt.date().isoformat()
            total_msgs += 1
            by_day[day]["messages"] += 1
            s = (m.get("entities") or {}).get("sentiment")
            if s and s.get("basic"):
                by_day[day]["classified"] += 1
                if s["basic"] == "Bullish":
                    bull += 1
                    by_day[day]["bull"] += 1
                elif s["basic"] == "Bearish":
                    bear += 1
                    by_day[day]["bear"] += 1
        pages += 1
        cur = data.get("cursor", {})
        if not cur.get("more"):
            break
        cursor_max = cur.get("max")
        if not cursor_max:
            break

    if total_msgs == 0:
        return 0
    classified = bull + bear
    spread = (bull - bear) / classified if classified else None
    asof = datetime.date.today().isoformat()

    # detail rows
    daily_rows = []
    for day, d in sorted(by_day.items(), reverse=True):
        c = d["classified"]
        daily_rows.append({"day": day, "messages": d["messages"], "classified": c,
                           "bull": d["bull"], "bear": d["bear"],
                           "spread": round((d["bull"] - d["bear"]) / c, 4) if c else None})
    db.replace_social_daily(ticker, daily_rows)

    # volume reading (count only)
    v_wrote = db.upsert_reading(asof, "ticker", ticker, "social_volume", SOURCE_STOCKTWITS,
                                float(total_msgs), None, count=total_msgs)
    # spread reading (sentiment = passthrough)
    s_wrote = 0
    if spread is not None:
        s_wrote = db.upsert_reading(asof, "ticker", ticker, "social_bull_bear", SOURCE_STOCKTWITS,
                                    round(spread, 4), round(spread, 4), count=classified)
    return (1 if v_wrote else 0) + (1 if s_wrote else 0)


# ---------------------------------------------------------------------------
# Phase 4b: AAII Investor Sentiment Survey (weekly, Thursday).
#
# aaii.com sits behind Imperva bot protection — every scripted HTTP client gets
# a JS challenge (403/"Pardon Our Interruption"), only a real browser passes.
# Deterministic workaround (Hong-approved 2026-08-07, option B): Firecrawl
# scrape API bypasses Imperva. Free tier = 1,000 credits/month; AAII needs 1
# scrape/week (~4 credits) — effectively free. Key via env FIRECRAWL_API_KEY.
# Fallback (option A): weekly Hermes cron with web_extract.
# ---------------------------------------------------------------------------

SOURCE_AAII = "aaii"

AAII_SURVEY_URL = "https://www.aaii.com/sentimentsurvey"
FIRECRAWL_API = "https://api.firecrawl.dev/v1/scrape"
FIRECRAWL_KEY_ENV = "FIRECRAWL_API_KEY"


def collect_aaii():
    """AAII weekly bull/bear/neutral survey via Firecrawl scrape.

    value = bull - bear (percentage points, e.g. 37.0 - 38.0 = -1.0);
    sentiment = spread_100 (value/100). asof = week-ending date (M/D/YYYY).
    Fail-open: no key -> 0; scrape/parse error -> 0.
    """
    import sentiment  # lazy
    import os
    import re

    key = os.environ.get(FIRECRAWL_KEY_ENV)
    if not key:
        logger.warning("FIRECRAWL_API_KEY not set — aaii provider disabled")
        return 0
    try:
        import requests
        r = requests.post(FIRECRAWL_API,
                          headers={"Authorization": "Bearer " + key},
                          json={"url": AAII_SURVEY_URL, "formats": ["markdown"]},
                          timeout=40)
        r.raise_for_status()
        md = (r.json().get("data") or {}).get("markdown", "")
        if not md:
            logger.error("AAII: empty Firecrawl markdown")
            return 0
        # First week row: date then Bullish/Neutral/Bearish percentages.
        m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})\s*([\d.]+)%\s*([\d.]+)%\s*([\d.]+)%", md)
        if not m:
            logger.error("AAII: week row not found in markdown")
            return 0
        mo, dd, yy, bull, _neutral, bear = m.groups()
        asof = f"{yy}-{mo.zfill(2)}-{dd.zfill(2)}"
        value = float(bull) - float(bear)
        sent = sentiment.normalize(value, "spread_100")
        return 1 if db.upsert_reading(asof, "market", None, "aaii_bull_bear_spread", SOURCE_AAII,
                                      value, sent, count=1) else 0
    except Exception as e:
        logger.error("AAII fetch failed: %s", e)
        return 0


# ---------------------------------------------------------------------------
# Phase 5: Alpha Vantage NEWS_SENTIMENT — market-scope aggregate (Hong-approved
# 2026-08-07). One call/day with index tickers; mean article score = market
# news mood. Key via env (ALPHA_VANTAGE_API_KEY), never in code. Fail-open.
# StockTwits (social_volume, per-ticker) still deferred.
# ---------------------------------------------------------------------------

SOURCE_AV = "alphavantage"

AV_NEWS_URL = "https://www.alphavantage.co/query"


def collect_av_news():
    """Market-wide news sentiment from Alpha Vantage NEWS_SENTIMENT.

    No tickers param: AV's tickers filter is INTERSECTION-based (articles must
    mention ALL tickers) — multi-ticker calls return 0 articles (verified live
    2026-08-07: SPY alone 50, SPY,QQQ 0). The no-ticker query returns today's
    top market news (financial_markets/earnings heavy) — the correct
    market-wide mood sample. value = mean overall_sentiment_score (-1..+1),
    count = articles, sentiment = passthrough. Fail-open: no key -> 0; error -> 0.
    """
    import sentiment  # lazy
    import os

    key = os.environ.get(sentiment.AV_API_KEY_ENV)
    if not key:
        logger.warning("ALPHA_VANTAGE_API_KEY not set — alphavantage provider disabled")
        return 0
    try:
        import requests
        r = requests.get(AV_NEWS_URL, params={
            "function": "NEWS_SENTIMENT",
            "limit": 50,
            "apikey": key,
        }, timeout=20)
        r.raise_for_status()
        data = r.json()
        if "feed" not in data:
            logger.error("AV: unexpected response: %s", str(data)[:120])
            return 0
        feed = data.get("feed") or []
        scores = []
        for art in feed:
            s = art.get("overall_sentiment_score")
            try:
                scores.append(float(s))
            except (TypeError, ValueError):
                continue
        if not scores:
            return 0
        value = sum(scores) / len(scores)
        asof = datetime.date.today().isoformat()
        sent = sentiment.normalize(value, "passthrough")
        return 1 if db.upsert_reading(asof, "market", None, "news_sentiment", SOURCE_AV,
                                      value, sent, count=len(scores)) else 0
    except Exception as e:
        logger.error("AV news sentiment fetch failed: %s", e)
        return 0


# ---------------------------------------------------------------------------
# Phase 4: surveys — NAAIM (no key). AAII deferred: aaii.com is WAF bot-blocked
# (403 on every scripted fetch incl. browser UA); browser-only, not suitable
# for a deterministic cron collector. FRED (UMich/Conf Board) removed 2026-08-07
# per Hong — consumer confidence is macro data, not market sentiment.
# ---------------------------------------------------------------------------

SOURCE_NAAIM = "naaim"

NAAIM_CHART_URL = "https://index.naaim.org/embeddable/chart"


def collect_naaim():
    """NAAIM Exposure Index (weekly, 0-100+) from the embeddable chart JSON.

    The page embeds the full series in a Symfony UX ChartJS data attribute
    (HTML-escaped JSON: &quot;). Parse it, take the latest point.
    value = index; sentiment = center_50 (>=50 exposure = bullish).
    """
    import sentiment  # lazy
    import re
    import html
    import json

    page = _fetch_text(NAAIM_CHART_URL)
    if not page:
        return 0
    m = re.search(r'data-symfony--ux-chartjs--chart-view-value="([^"]*)"', page)
    if not m:
        logger.error("NAAIM: chart data attribute not found")
        return 0
    try:
        data = json.loads(html.unescape(m.group(1)))
        labels = data["data"]["labels"]
        values = data["data"]["datasets"][0]["data"]
    except Exception as e:
        logger.error("NAAIM chart parse failed: %s", e)
        return 0
    if not labels or len(labels) != len(values):
        return 0
    asof, value = labels[-1], float(values[-1])
    sent = sentiment.normalize(value, "center_50")
    return 1 if db.upsert_reading(asof, "market", None, "naaim_exposure", SOURCE_NAAIM,
                                  value, sent, count=1) else 0


# ---------------------------------------------------------------------------
# Phase 3: CBOE (VIX + put/call) and FINRA short interest — free files, no keys
# ---------------------------------------------------------------------------

SOURCE_CBOE = "cboe"
SOURCE_FINRA = "finra"

VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
CBOE_DAILY_URL = "https://www.cboe.com/markets/us/options/market-statistics/daily/"
FINRA_FILES_URL = "https://www.finra.org/finra-data/browse-catalog/equity-short-interest/files"
FINRA_FILE_URL = "https://cdn.finra.org/equity/otcmarket/biweekly/shrt{date}.csv"

# Plain UA: FINRA's WAF 403s the full Chrome UA string but accepts "Mozilla/5.0";
# CBOE accepts it too. (StockTwits, in contrast, needs a browser UA — handled
# separately in its own collector in Phase 5.)
_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _fetch_text(url, timeout=20):
    """Fetch text with browser UA; returns None on failure (fail-open)."""
    import requests
    try:
        r = requests.get(url, timeout=timeout, headers=_HEADERS)
        r.raise_for_status()
        return r.text
    except Exception as e:
        logger.error("fetch failed for %s: %s", url.split("?")[0], e)
        return None


def collect_cboe():
    """VIX (file history -> real percentile) + Equity/Index P/C (page ratios).

    VIX: parse VIX_History.csv, take the last 253 closes (1y trailing window),
    compute percentile_inv of the latest close -> real sentiment from day one.
    P/C: parse the daily stats page's embedded ratios JSON; sentiment uses the
    strip's own history (percentile_inv) — gray until history accrues.
    Returns rows written.
    """
    import sentiment  # lazy
    import csv
    import io

    n = 0
    # --- VIX ---
    text = _fetch_text(VIX_URL)
    if text:
        try:
            rows = list(csv.DictReader(io.StringIO(text)))
            if rows:
                closes = []
                for r in rows:
                    try:
                        closes.append(float(r["CLOSE"]))
                    except (ValueError, KeyError, TypeError):
                        continue
                if closes:
                    hist = closes[-253:]          # trailing 1y incl. latest
                    latest = closes[-1]
                    asof = rows[-1]["DATE"].strip()
                    # DATE is MM/DD/YYYY -> YYYY-MM-DD
                    try:
                        m, d, y = asof.split("/")
                        asof = f"{y}-{m.zfill(2)}-{d.zfill(2)}"
                    except ValueError:
                        pass
                    sent = sentiment.normalize(latest, "percentile_inv", hist)
                    if db.upsert_reading(asof, "market", None, "vix", SOURCE_CBOE,
                                         latest, sent, count=1):
                        n += 1
        except Exception as e:
            logger.error("VIX parse failed: %s", e)

    # --- Equity + Index P/C from daily page embedded ratios ---
    page = _fetch_text(CBOE_DAILY_URL)
    if page:
        import re
        try:
            # Embedded JSON is double-escaped (Next.js flight payload: \\\"...\\\");
            # strip backslashes, then match {"name":"EQUITY PUT/CALL RATIO","value":"0.57"}
            clean = page.replace("\\", "")
            found = {}
            for name, val in re.findall(r'"name":"([^"]+)","value":"([\d.]+)"', clean):
                val = float(val)
                if name == "EQUITY PUT/CALL RATIO":
                    found["cboe_pc_equity"] = val
                elif name == "INDEX PUT/CALL RATIO":
                    found["cboe_pc_index"] = val
            today = datetime.date.today().strftime("%Y-%m-%d")
            for metric, val in found.items():
                hist = db.history_for(metric, limit=252)
                sent = sentiment.normalize(val, "percentile_inv", hist) if hist else None
                if db.upsert_reading(today, "market", None, metric, SOURCE_CBOE, val, sent, count=1):
                    n += 1
        except Exception as e:
            logger.error("CBOE P/C parse failed: %s", e)

    return n


def collect_finra_short_interest():
    """FINRA bi-weekly short interest: days-to-cover per universe ticker.

    Discovers the latest settlement file from the FINRA catalog page, fetches
    the pipe-delimited CSV, keeps BREADTH_UNIVERSE tickers with a sane
    days-to-cover (999.99 = sentinel for zero avg volume -> skipped).
    asof = settlement date (point-in-time honest; FINRA publishes ~2 weeks after).
    sentiment = percentile_inv over the strip's own history (gray until accrues).
    Incremental: skips settlements already stored.
    """
    import sentiment  # lazy
    import re

    page = _fetch_text(FINRA_FILES_URL)
    if not page:
        return 0
    dates = sorted(set(re.findall(r"shrt(\d{8})\.csv", page)))
    if not dates:
        logger.error("FINRA: no settlement files found on catalog page")
        return 0
    latest = dates[-1]
    asof = f"{latest[:4]}-{latest[4:6]}-{latest[6:]}"
    last_stored = db.latest_reading_date_for("short_interest_dtc", SOURCE_FINRA)
    if last_stored and asof <= last_stored:
        return 0  # already collected this settlement

    text = _fetch_text(FINRA_FILE_URL.format(date=latest))
    if not text:
        return 0
    n = 0
    # Keep only the latest per ticker (file is per-settlement, one row per ticker)
    want = set(BREADTH_UNIVERSE)
    hist = db.history_for("short_interest_dtc", limit=52)
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) < 10:
            continue
        sym = parts[1].strip().upper()
        if sym not in want:
            continue
        try:
            dtc = float(parts[9].strip())  # daysToCoverQuantity (10th field)
        except (ValueError, IndexError):
            continue
        if dtc >= 999.0:  # sentinel: no meaningful avg daily volume
            continue
        sent = sentiment.normalize(dtc, "percentile_inv", hist) if hist else None
        if db.upsert_reading(asof, "ticker", sym, "short_interest_dtc", SOURCE_FINRA,
                             dtc, sent, count=int(float(parts[5] or 0))):
            n += 1
    return n


def run_all(sources=None):
    """Run registered collectors; returns {source: rows_written} (fail-open each)."""
    import sentiment
    return sentiment.run_collectors(sources=sources)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import json
    print(json.dumps(run_all(), indent=1))
