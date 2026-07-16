"""
Yahoo Finance Quote Fetcher
KAN-19: Yahoo Finance Quote Integration
"""
import json
import math
import time
from datetime import datetime, timedelta
from typing import Optional
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


def get_quote(ticker: str, use_cache: bool = True) -> dict:
    """Fetch single quote with caching."""
    ticker_upper = ticker.upper()
    now = time.time()
    
    if use_cache and ticker in _cache:
        cached_data, cached_time = _cache[ticker]
        if now - cached_time < _cache_ttl:
            return cached_data
    
    try:
        ticker_obj = yfinance.Ticker(ticker)
        info = ticker_obj.info
        
        # Initialize return values as None
        ret_1d = ret_1w = ret_1m = ret_3m = ret_ytd = ret_1y = ret_5y = None
        
        # Get historical data for performance calculation
        try:
            hist = ticker_obj.history(period="5y")
            if len(hist) > 0:
                latest_price = safe_float(hist['Close'].iloc[-1])
                if latest_price:
                    # Calculate returns for each period based on chart timeframes
                    from datetime import datetime as dt, timedelta
                    now_local = dt.now(hist.index.tz)
                    
                    # 1D (previous close to now - use yesterday's close)
                    prev_close = safe_float(hist['Close'].iloc[-2]) if len(hist) > 1 else latest_price
                    ret_1d = safe_ret(latest_price, prev_close)
                    
                    # 1W (7 days ago)
                    one_wk = hist[hist.index >= now_local - timedelta(days=7)]
                    ret_1w = safe_ret(latest_price, safe_float(one_wk['Close'].iloc[0])) if len(one_wk) > 1 else None
                    
                    # 1M (30 days ago)
                    one_mo = hist[hist.index >= now_local - timedelta(days=30)]
                    ret_1m = safe_ret(latest_price, safe_float(one_mo['Close'].iloc[0])) if len(one_mo) > 1 else None
                    
                    # 3M (90 days ago)
                    three_mo = hist[hist.index >= now_local - timedelta(days=90)]
                    ret_3m = safe_ret(latest_price, safe_float(three_mo['Close'].iloc[0])) if len(three_mo) > 1 else None
                    
                    # YTD performance
                    ytd_start = hist[hist.index >= '2026-01-01']
                    ret_ytd = safe_ret(latest_price, safe_float(ytd_start['Close'].iloc[0])) if len(ytd_start) > 1 else None
                    
                    # 1Y performance
                    one_yr = hist[hist.index >= '2025-03-31']
                    ret_1y = safe_ret(latest_price, safe_float(one_yr['Close'].iloc[0])) if len(one_yr) > 1 else None
                    
                    # 5Y performance
                    five_yr = hist[hist.index >= '2021-03-31']
                    ret_5y = safe_ret(latest_price, safe_float(five_yr['Close'].iloc[0])) if len(five_yr) > 1 else None
        except Exception as e:
            print(f"[ERROR get_quote history] {type(e).__name__}: {e}", file=sys.stderr)
        
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
            "ret_1d": ret_1d,
            "ret_1w": ret_1w,
            "ret_1m": ret_1m,
            "ret_3m": ret_3m,
            "ret_ytd": ret_ytd,
            "ret_1y": ret_1y,
            "ret_5y": ret_5y,
            "timestamp": now
        }
        
        _cache[ticker] = (quote, now)
        return quote
        
    except Exception as e:
        return {"ticker": ticker.upper(), "error": str(e)}


def get_quotes(tickers: list[str], use_cache: bool = True) -> dict[str, dict]:
    """Fetch multiple quotes."""
    results = {}
    for ticker in tickers:
        try:
            results[ticker.upper()] = get_quote(ticker, use_cache)
        except Exception as e:
            print(f"[ERROR get_quote({ticker})] {type(e).__name__}: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            results[ticker.upper()] = {"ticker": ticker.upper(), "error": str(e)}
        time.sleep(0.1)  # Rate limiting
    return results


def get_quotes_json(tickers: list[str]) -> str:
    """Return quotes as JSON string."""
    return json.dumps(get_quotes(tickers))


if __name__ == "__main__":
    # Test
    test_tickers = ["SPY", "QQQ", "TLT"]
    print(get_quotes_json(test_tickers))