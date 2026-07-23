"""
52-week-high scanner for Alpha Terminal.

Source of the candidate list: finvizfinance screener (signal="New High",
filtered by Exchange = NYSE / NASDAQ). This returns the actual full set of
NYSE/NASDAQ stocks at their 52-week high for the last closed session --
no hand-built universe needed.

finviz supplies Ticker/Company/Sector/Exchange/Price/Volume. For the precise
distance-to-high we optionally enrich with yfinance (trailing 252-day high);
if yfinance is unavailable the stock is stored at pct_off 0.0 (it is, by
definition via the New High signal, at its high).

Results are persisted to the SQLite `year_highs` table (db.py) once per
trading day at 5pm EST/EDT.
"""
import logging

logger = logging.getLogger("alpha-terminal.year-highs")

# How close to the 52w high a stock must be to be listed (percent below high).
AT_HIGH_THRESHOLD_PCT = 2.0
# Trailing window for the 52-week high (trading days).
HIGH_WINDOW = 252

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


def get_candidates():
    """Return finviz new-high candidates as list of dicts.

    Each: {ticker, exchange, sector, company, price, volume}
    """
    if not FINVIZ_AVAILABLE:
        logger.error("finvizfinance not available; cannot scan")
        return []
    candidates = []
    for exchange in _EXCHANGES:
        try:
            ov = Overview()
            ov.set_filter(signal="New High", filters_dict={"Exchange": exchange})
            df = ov.screener_view()
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                candidates.append({
                    "ticker": str(row.get("Ticker", "")).strip().upper(),
                    "exchange": str(row.get("Exchange", exchange)).strip().upper(),
                    "sector": str(row.get("Sector", "") or ""),
                    "company": str(row.get("Company", "") or ""),
                    "price": _to_float(row.get("Price")),
                    "volume": _to_int(row.get("Volume")),
                })
        except Exception as e:
            logger.warning(f"finviz scan failed for {exchange}: {e}")
            continue
    return candidates


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


def _enrich_pct_off(ticker, price):
    """Compute (pct_off, high_52w) via yfinance; fall back to (0.0, price)."""
    if not YFINANCE_AVAILABLE or price is None:
        return 0.0, price
    try:
        data = yf.Ticker(ticker).history(period="1y")["Close"].dropna()
        if data.empty or len(data) < 30:
            return 0.0, price
        high_52w = float(data.tail(HIGH_WINDOW).max())
        if high_52w <= 0:
            return 0.0, price
        pct_off = (float(data.iloc[-1]) - high_52w) / high_52w * 100.0
        return round(pct_off, 4), round(high_52w, 4)
    except Exception as e:
        logger.debug(f"pct_off enrich failed for {ticker}: {e}")
        return 0.0, price


def scan_year_highs(threshold_pct=AT_HIGH_THRESHOLD_PCT):
    """Build rows for all NYSE/NASDAQ new-high candidates.

    Each row: dict(ticker, exchange, sector, company, close, high_52w,
    pct_off, volume). pct_off is within `threshold_pct` of the high by
    construction (New High signal); enrichment keeps it precise.
    """
    candidates = get_candidates()
    rows = []
    for c in candidates:
        if not c["ticker"]:
            continue
        pct_off, high_52w = _enrich_pct_off(c["ticker"], c["price"])
        if pct_off < -threshold_pct:
            continue
        close = c["price"] if c["price"] is not None else high_52w
        rows.append({
            "ticker": c["ticker"],
            "exchange": c["exchange"],
            "sector": c["sector"],
            "company": c.get("company", ""),
            "close": round(close, 4) if close is not None else None,
            "high_52w": high_52w,
            "pct_off": pct_off,
            "volume": c["volume"] or 0,
        })
    rows.sort(key=lambda r: (r["pct_off"] is None, r["pct_off"] if r["pct_off"] is not None else 999, r["ticker"]))
    return rows


def store_today_snapshot(threshold_pct=AT_HIGH_THRESHOLD_PCT, force=False):
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
    rows = scan_year_highs(threshold_pct=threshold_pct)
    count = db.store_year_highs(date_str, rows)
    logger.info(f"year-highs: stored {count} rows for {date_str}")
    return date_str, count, False
