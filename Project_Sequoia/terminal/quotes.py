"""
Yahoo Finance Quote Fetcher
KAN-19: Yahoo Finance Quote Integration

Batched history (2026-08-09): return percentages for the whole watchlist now
come from ONE `yf.download(tickers, period='5y')` call instead of one 5y
history fetch per ticker. Display fields still come from per-ticker `info`
(5s-cached). Falls back to per-ticker history if the batch download fails.
"""
import json
import math
import time
from datetime import datetime, timedelta
from typing import Optional
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
import yfinance
import sys
import traceback

# Cache for quotes (5s TTL for live-ish feel)
_cache = {}
_cache_ttl = 5


def safe_float(val):
    """Safely convert value to float, returning None if invalid."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def safe_ret(price_now, price_past):
    """Safely calculate percentage return, returning None on error."""
    if price_now is None or price_past is None or price_past == 0:
        return None
    try:
        ret = ((price_now / price_past) - 1) * 100
        return None if math.isnan(ret) or math.isinf(ret) else ret
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _download_batch(tickers, period="5y"):
    """One batched 5y daily history download for all tickers.

    Returns the yf.download frame, or None on failure (caller falls back to
    per-ticker history). Single network round-trip instead of one per ticker.
    """
    if not tickers:
        return None
    try:
        frame = yfinance.download(
            tickers, period=period, group_by="ticker",
            auto_adjust=False, progress=False, threads=False,
        )
        if frame is None or frame.empty:
            return None
        return frame
    except Exception as e:
        print(f"[quotes] batch download failed for {len(tickers)} tickers: {e}",
              file=sys.stderr)
        return None


def _batch_series(batch, ticker):
    """Extract one ticker's OHLCV frame from a yf.download batch.

    Multi-ticker batches have MultiIndex columns (ticker, field); single-ticker
    batches have plain columns. Returns None when the ticker is absent.
    """
    if batch is None:
        return None
    try:
        if getattr(batch.columns, "nlevels", 1) > 1:
            return batch[ticker]
        return batch
    except (KeyError, AttributeError):
        return None


def _compute_returns(hist, latest_price=None):
    """Compute the 9 return percentages from a Close series.

    Relative lookback windows (7/30/90/180/365/730/1825 days) + calendar-year
    YTD, anchored to 'now' — never goes stale like the old hardcoded dates.
    """
    rets = {"ret_1d": None, "ret_1w": None, "ret_1m": None, "ret_3m": None,
            "ret_6m": None, "ret_ytd": None, "ret_1y": None, "ret_2y": None,
            "ret_5y": None}
    if hist is None or len(hist) == 0:
        return rets
    try:
        close = hist["Close"].dropna()
        if close.empty:
            return rets
        latest = latest_price if latest_price is not None else float(close.iloc[-1])
        if not latest:
            return rets
        prev_close = safe_float(close.iloc[-2]) if len(close) > 1 else latest
        rets["ret_1d"] = safe_ret(latest, prev_close)

        tz = hist.index.tz
        now_local = datetime.now(tz) if tz else datetime.now()

        def lookback(days):
            seg = close[close.index >= now_local - timedelta(days=days)]
            return safe_ret(latest, safe_float(seg.iloc[0])) if len(seg) > 1 else None

        rets["ret_1w"] = lookback(7)
        rets["ret_1m"] = lookback(30)
        rets["ret_3m"] = lookback(90)
        rets["ret_6m"] = lookback(180)

        ytd_start_ts = pd.Timestamp(now_local.year, 1, 1, tz=tz)
        ytd_start = close[close.index >= ytd_start_ts]
        rets["ret_ytd"] = safe_ret(latest, safe_float(ytd_start.iloc[0])) if len(ytd_start) > 1 else None

        rets["ret_1y"] = lookback(365)
        rets["ret_2y"] = lookback(730)
        rets["ret_5y"] = lookback(1825)
    except Exception as e:
        print(f"[ERROR _compute_returns] {type(e).__name__}: {e}", file=sys.stderr)
    return rets


def get_quote(ticker: str, use_cache: bool = True, _batch=None) -> dict:
    """Fetch single quote with caching.

    `_batch` (optional): a shared yf.download frame from get_quotes. When
    provided, the per-ticker 5y history fetch is skipped and the return
    percentages are computed from the batch.
    """
    ticker_upper = ticker.upper()
    now = time.time()
    
    if use_cache and ticker in _cache:
        cached_data, cached_time = _cache[ticker]
        if now - cached_time < _cache_ttl:
            return cached_data
    
    try:
        ticker_obj = yfinance.Ticker(ticker)
        info = ticker_obj.info
        
        # History: batched frame if provided, otherwise the legacy per-ticker 5y fetch
        if _batch is not None:
            hist = _batch_series(_batch, ticker)
        else:
            try:
                hist = ticker_obj.history(period="5y")
            except Exception as e:
                print(f"[ERROR get_quote history] {type(e).__name__}: {e}", file=sys.stderr)
                hist = None
        rets = _compute_returns(hist)
        
        quote = {
            "ticker": ticker.upper(),
            "name": info.get("shortName", info.get("longName", ticker)),
            "price": safe_float(info.get("currentPrice", info.get("regularMarketPrice"))),
            "change": safe_float(info.get("regularMarketChange")),
            "change_pct": safe_float(info.get("regularMarketChangePercent")),
            "open": safe_float(info.get("regularMarketOpen")),
            "high": safe_float(info.get("regularMarketDayHigh")),
            "low": safe_float(info.get("regularMarketDayLow")),
            "volume": safe_float(info.get("regularMarketVolume")),
            "avg_volume": safe_float(info.get("averageVolume")),
            "market_cap": safe_float(info.get("marketCap")),
            "pe_ratio": safe_float(info.get("trailingPE")),
            "dividend_yield": safe_float(info.get("dividendYield")),
            "52w_high": safe_float(info.get("fiftyTwoWeekHigh")),
            "52w_low": safe_float(info.get("fiftyTwoWeekLow")),
            "ret_1d": rets["ret_1d"],
            "ret_1w": rets["ret_1w"],
            "ret_1m": rets["ret_1m"],
            "ret_3m": rets["ret_3m"],
            "ret_6m": rets["ret_6m"],
            "ret_ytd": rets["ret_ytd"],
            "ret_1y": rets["ret_1y"],
            "ret_2y": rets["ret_2y"],
            "ret_5y": rets["ret_5y"],
            "timestamp": now
        }
        
        _cache[ticker] = (quote, now)
        return quote
        
    except Exception as e:
        return {"ticker": ticker.upper(), "error": str(e)}


def get_quotes(tickers: list[str], use_cache: bool = True) -> dict[str, dict]:
    """Fetch multiple quotes.

    Downloads 5y history ONCE for the whole list (yf.download batch), then
    per-ticker display fields from `info` (5s-cached) fetched in PARALLEL
    (small thread pool — 14 sequential .info calls ≈ 3s+; pooled ≈ 0.6s).
    Falls back to per-ticker history if the batch fails. One history
    round-trip instead of one per ticker.
    """
    tickers = [t.strip().upper() for t in tickers]
    batch = _download_batch(tickers)
    results = {}
    with ThreadPoolExecutor(max_workers=min(6, len(tickers) or 1)) as pool:
        futures = {pool.submit(get_quote, t, use_cache, batch): t for t in tickers}
        for fut in futures:
            ticker = futures[fut]
            try:
                results[ticker] = fut.result()
            except Exception as e:
                print(f"[ERROR get_quote({ticker})] {type(e).__name__}: {e}", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                results[ticker] = {"ticker": ticker, "error": str(e)}
    return results


def get_quotes_json(tickers: list[str]) -> str:
    """Return quotes as JSON string."""
    return json.dumps(get_quotes(tickers))


if __name__ == "__main__":
    # Test
    test_tickers = ["SPY", "QQQ", "TLT"]
    print(get_quotes_json(test_tickers))
