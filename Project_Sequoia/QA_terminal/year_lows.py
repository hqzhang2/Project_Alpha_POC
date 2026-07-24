"""
52-week-low scanner for Alpha Terminal.

Source of the candidate list: finvizfinance screener (signal="New Low",
filtered by Exchange = NYSE / NASDAQ). This returns the actual full set of
NYSE/NASDAQ stocks at their 52-week low for the last closed session --
no hand-built universe needed.

Results are persisted to the SQLite `year_lows` table (db.py) once per
trading day at 5pm EST/EDT.
"""
import logging
from datetime import datetime

logger = logging.getLogger("alpha-terminal.year-lows")

AT_LOW_THRESHOLD_PCT = 2.0
LOW_WINDOW = 252

try:
    from finvizfinance.screener.overview import Overview
    FINVIZ_AVAILABLE = True
except ImportError:
    FINVIZ_AVAILABLE = False
    Overview = None

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False
    yf = None

_EXCHANGES = ["NYSE", "NASDAQ"]


def _clean_ticker(ticker):
    """Strip duplicated first character from finviz tickers (e.g. AADM -> ADM)."""
    if len(ticker) > 1 and ticker[0] == ticker[1]:
        return ticker[1:]
    return ticker


def get_candidates():
    """Return finviz new-low candidates as list of dicts.

    Each: {ticker, exchange, sector, company, price, volume, market_cap}
    """
    if not FINVIZ_AVAILABLE:
        logger.error("finvizfinance not available; cannot scan")
        return []
    candidates = []
    for exchange in _EXCHANGES:
        try:
            ov = Overview()
            ov.set_filter(signal="New Low", filters_dict={"Exchange": exchange})
            df = ov.screener_view()
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                mc = row.get("Market Cap")
                if isinstance(mc, float) and mc > 0:
                    market_cap = mc
                elif isinstance(mc, str):
                    market_cap = _parse_market_cap(mc)
                else:
                    market_cap = None

                candidates.append({
                    "ticker": _clean_ticker(str(row.get("Ticker", "")).strip().upper()),
                    "exchange": str(row.get("Exchange", exchange)).strip().upper(),
                    "sector": str(row.get("Sector", "") or ""),
                    "company": str(row.get("Company", "") or ""),
                    "price": _to_float(row.get("Price")),
                    "volume": _to_int(row.get("Volume")),
                    "market_cap": market_cap,
                })
        except Exception as e:
            logger.warning(f"finviz scan failed for {exchange}: {e}")
            continue
    return candidates


def _parse_market_cap(v):
    """Parse market cap string like '4.16B' or '1.27T' to float."""
    if v is None:
        return None
    try:
        s = str(v).strip().upper()
        if s.endswith('T'):
            return float(s[:-1]) * 1e12
        elif s.endswith('B'):
            return float(s[:-1]) * 1e9
        elif s.endswith('M'):
            return float(s[:-1]) * 1e6
        else:
            return float(s)
    except Exception:
        return None


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _enrich_pct_from_low(ticker, price):
    """Compute (pct_from_low, low_52w) via yfinance; fall back to (0.0, price)."""
    if not YFINANCE_AVAILABLE or price is None:
        return 0.0, price
    try:
        data = yf.Ticker(ticker).history(period="1y")["Close"].dropna()
        if data.empty or len(data) < 30:
            return 0.0, price
        low_52w = float(data.tail(LOW_WINDOW).min())
        if low_52w <= 0:
            return 0.0, price
        pct_from_low = (float(data.iloc[-1]) - low_52w) / low_52w * 100.0
        return round(pct_from_low, 4), round(low_52w, 4)
    except Exception as e:
        logger.debug(f"pct_from_low enrich failed for {ticker}: {e}")
        return 0.0, price


def scan_year_lows(threshold_pct=AT_LOW_THRESHOLD_PCT):
    """Build rows for all NYSE/NASDAQ new-low candidates."""
    candidates = get_candidates()
    rows = []
    for c in candidates:
        if not c["ticker"]:
            continue
        pct_from_low, low_52w = _enrich_pct_from_low(c["ticker"], c["price"])
        if pct_from_low > threshold_pct:
            continue
        close = c["price"] if c["price"] is not None else low_52w
        rows.append({
            "ticker": c["ticker"],
            "exchange": c["exchange"],
            "sector": c["sector"],
            "company": c.get("company", ""),
            "close": round(close, 4) if close is not None else None,
            "low_52w": low_52w,
            "pct_from_low": pct_from_low,
            "volume": c["volume"] or 0,
            "market_cap": c.get("market_cap"),
        })
    rows.sort(key=lambda r: (r["pct_from_low"] is None, r["pct_from_low"] if r["pct_from_low"] is not None else 999, r["ticker"]))
    return rows


def store_today_snapshot(threshold_pct=AT_LOW_THRESHOLD_PCT, force=False):
    """Scan + store the snapshot for today (EST) if not already stored.

    If it's before 9:30am ET (market open), the snapshot is stored under
    the previous trading day's date to avoid labelling pre-market data
    with the current (unfinished) day.

    force=True overwrites an existing snapshot for that date.
    Returns (date_str, count, already_existed: bool).
    """
    import db
    date_str = _snapshot_date()
    existing = db.get_year_lows(date_str)
    if existing and not force:
        logger.info(f"year-lows: {date_str} already stored ({len(existing)} rows); skip")
        return date_str, len(existing), True
    rows = scan_year_lows(threshold_pct=threshold_pct)
    count = db.store_year_lows(date_str, rows)
    logger.info(f"year-lows: stored {count} rows for {date_str}")
    return date_str, count, False


def _snapshot_date():
    """Return today's date in EST, or yesterday if before 9:30am ET."""
    import db
    now = datetime.now()
    try:
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        now_et = now
    if now_et.hour < 9 or (now_et.hour == 9 and now_et.minute < 30):
        from datetime import timedelta
        prev = now_et - timedelta(days=1)
        return prev.strftime("%Y-%m-%d")
    return db.today_est_str()
