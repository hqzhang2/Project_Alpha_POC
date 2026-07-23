"""
52-week-high scanner for Alpha Terminal.

Scans the NYSE/NASDAQ large-cap universe (S&P 500 + NASDAQ-100, see universe.py)
and returns stocks trading within `AT_HIGH_THRESHOLD_PCT` of their trailing
252-day high at the last closed session. Results are persisted to the SQLite
`year_highs` table (db.py) once per trading day at 5pm EST/EDT.

A stock is "at its 52-week high" when its most-recent close is within
`AT_HIGH_THRESHOLD_PCT` (default 2%) below the high (pct_off >= -threshold).
"""
import logging

logger = logging.getLogger("alpha-terminal.year-highs")

# How close to the 52w high a stock must be to be listed (percent below high).
AT_HIGH_THRESHOLD_PCT = 2.0
# Trailing window for the 52-week high (trading days).
HIGH_WINDOW = 252


def _build_universe():
    """Return the NYSE/NASDAQ large-cap universe."""
    from universe import get_universe
    return get_universe()


def _exchange_for(code):
    """Map a yfinance `info['exchange']` code to NASDAQ / NYSE."""
    from universe import EXCHANGE_MAP
    return EXCHANGE_MAP.get(code, code or "")


def _fetch_history(ticker):
    """Return a pandas Series of daily Close prices for the trailing 1y."""
    import yfinance as yf
    data = yf.Ticker(ticker).history(period="1y")
    if data.empty:
        return None
    return data["Close"].dropna()


def scan_year_highs(universe=None, threshold_pct=AT_HIGH_THRESHOLD_PCT):
    """Scan the universe and return rows of stocks near their 52w high.

    Each row: dict(ticker, exchange, sector, close, high_52w, pct_off, volume)
    """
    import yfinance as yf
    universe = universe or _build_universe()
    rows = []
    for ticker in universe:
        try:
            hist = _fetch_history(ticker)
            if hist is None or len(hist) < 30:
                continue
            close = float(hist.iloc[-1])
            high_52w = float(hist.tail(HIGH_WINDOW).max())
            if high_52w <= 0:
                continue
            pct_off = (close - high_52w) / high_52w * 100.0
            # Keep only stocks within `threshold_pct` BELOW the 52w high.
            if pct_off < -threshold_pct:
                continue
            info = yf.Ticker(ticker).info
            exchange = _exchange_for(info.get("exchange", ""))
            sector = info.get("sector", "") or ""
            volume = int(hist.iloc[-1]) if hist.iloc[-1] else 0
            rows.append({
                "ticker": ticker,
                "exchange": exchange,
                "sector": sector,
                "close": round(close, 4),
                "high_52w": round(high_52w, 4),
                "pct_off": round(pct_off, 4),
                "volume": volume,
            })
        except Exception as e:
            logger.debug(f"year-highs: skip {ticker}: {e}")
            continue
    rows.sort(key=lambda r: r["pct_off"])
    return rows


def store_today_snapshot(universe=None, threshold_pct=AT_HIGH_THRESHOLD_PCT,
                         force=False):
    """Scan + store the snapshot for today (EST) if not already stored.

    force=True overwrites an existing snapshot for today.
    Returns (date_str, count, already_existed: bool).
    """
    import db
    date_str = db.today_est_str()
    existing = db.get_year_highs(date_str)
    if existing and not force:
        logger.info(f"year-highs: {date_str} already stored ({len(existing)} rows); skip")
        return date_str, len(existing), True
    rows = scan_year_highs(universe=universe, threshold_pct=threshold_pct)
    count = db.store_year_highs(date_str, rows)
    logger.info(f"year-highs: stored {count} rows for {date_str}")
    return date_str, count, False
