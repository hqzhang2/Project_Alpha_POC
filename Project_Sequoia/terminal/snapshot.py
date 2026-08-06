"""
Shared scanner utilities for Alpha Terminal snapshots (52-week highs/lows).

Provides generic finviz scanning, ticker cleanup, market-cap parsing,
and snapshot-date logic used by both year_highs.py and year_lows.py.
"""

import logging
from datetime import datetime, timedelta

import config

logger = logging.getLogger("alpha-terminal.snapshot")

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

EXCHANGES = config.SCAN_EXCHANGES


def clean_ticker(ticker):
    """Strip duplicated first character from finviz tickers (e.g. AADM -> ADM)."""
    if len(ticker) > 1 and ticker[0] == ticker[1]:
        return ticker[1:]
    return ticker


def parse_market_cap(v):
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


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def snapshot_date():
    """Return today's date in EST, or yesterday if before 9:30am ET."""
    import db
    now = datetime.now()
    try:
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        now_et = now
    if now_et.hour < 9 or (now_et.hour == 9 and now_et.minute < 30):
        prev = now_et - timedelta(days=1)
        return prev.strftime("%Y-%m-%d")
    return db.today_est_str()


def scan_candidates(signal):
    """Return finviz candidates for a given signal ('New High' or 'New Low').

    Each candidate: {ticker, exchange, sector, company, price, volume, market_cap}
    """
    if not FINVIZ_AVAILABLE:
        logger.error("finvizfinance not available; cannot scan")
        return []
    candidates = []
    for exchange in EXCHANGES:
        try:
            ov = Overview()
            ov.set_filter(signal=signal, filters_dict={"Exchange": exchange})
            df = ov.screener_view()
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                mc = row.get("Market Cap")
                if isinstance(mc, float) and mc > 0:
                    market_cap = mc
                elif isinstance(mc, str):
                    market_cap = parse_market_cap(mc)
                else:
                    market_cap = None
                candidates.append({
                    "ticker": clean_ticker(str(row.get("Ticker", "")).strip().upper()),
                    "exchange": str(row.get("Exchange", exchange)).strip().upper(),
                    "sector": str(row.get("Sector", "") or ""),
                    "company": str(row.get("Company", "") or ""),
                    "price": to_float(row.get("Price")),
                    "volume": to_int(row.get("Volume")),
                    "market_cap": market_cap,
                })
        except Exception as e:
            logger.warning(f"finviz scan failed for {exchange} ({signal}): {e}")
            continue
    return candidates


def enrich_yfinance(ticker, price, window=252, agg="max"):
    """Fetch yfinance trailing high/low and compute pct from extreme.

    agg='max': compute pct_off from 52w high (used for highs).
    agg='min': compute pct_from_low (used for lows).
    Returns (pct, extreme_value).
    """
    if not YFINANCE_AVAILABLE or price is None:
        return 0.0, price
    try:
        data = yf.Ticker(ticker).history(period="1y")["Close"].dropna()
        if data.empty or len(data) < 30:
            return 0.0, price
        extreme = float(data.tail(window).max()) if agg == "max" else float(data.tail(window).min())
        if extreme <= 0:
            return 0.0, price
        pct = (float(data.iloc[-1]) - extreme) / extreme * 100.0
        return round(pct, 4), round(extreme, 4)
    except Exception as e:
        logger.debug(f"yfinance enrich failed for {ticker}: {e}")
        return 0.0, price


def build_rows(candidates, enrich_fn, threshold_pct, col_prefix, pct_key="pct_off"):
    """Build row dicts from candidates.

    enrich_fn(ticker, price) -> (pct, extreme_value)
    col_prefix: 'high' for 52w-high columns, 'low' for 52w-low columns.
    pct_key: column name for the pct value ('pct_off' for highs, 'pct_from_low' for lows).
    """
    rows = []
    for c in candidates:
        if not c["ticker"]:
            continue
        pct_val, extreme = enrich_fn(c["ticker"], c["price"])
        if col_prefix == "high" and pct_val < -threshold_pct:
            continue
        if col_prefix == "low" and pct_val > threshold_pct:
            continue
        close = c["price"] if c["price"] is not None else extreme
        row = {
            "ticker": c["ticker"],
            "exchange": c["exchange"],
            "sector": c["sector"],
            "company": c.get("company", ""),
            "close": round(close, 4) if close is not None else None,
            "volume": c["volume"] or 0,
            "market_cap": c.get("market_cap"),
        }
        row[f"{col_prefix}_52w"] = extreme
        row[pct_key] = pct_val
        rows.append(row)
    rows.sort(key=lambda r: (r[pct_key] is None, r[pct_key] if r[pct_key] is not None else 999, r["ticker"]))
    return rows


def store_today(table_name, get_fn, store_fn, scan_signal, enrich_fn, threshold_pct, logger_name, force=False):
    """Generic snapshot store: scan finviz, enrich, persist.

    get_fn(db_fn): e.g., db.get_year_highs(date_str)
    store_fn(db_fn): e.g., db.store_year_highs(date_str, rows)
    """
    import db
    date_str = snapshot_date()
    existing = get_fn(date_str)
    if existing and not force:
        logger.info(f"{logger_name}: {date_str} already stored ({len(existing)} rows); skip")
        return date_str, len(existing), True
    candidates = scan_candidates(scan_signal)
    col_prefix = "high" if "high" in scan_signal.lower() else "low"
    pct_key = "pct_off" if col_prefix == "high" else "pct_from_low"
    rows = build_rows(candidates, enrich_fn, threshold_pct, col_prefix, pct_key=pct_key)
    count = store_fn(date_str, rows)
    logger.info(f"{logger_name}: stored {count} rows for {date_str}")
    return date_str, count, False