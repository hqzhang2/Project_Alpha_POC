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
