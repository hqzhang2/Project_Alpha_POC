"""
Yahoo Finance Quote Fetcher
KAN-19: Yahoo Finance Quote Integration
"""
import json
import time
from datetime import datetime, timedelta
from typing import Optional
import yfinance
import sys
import traceback

# Cache for quotes (5s TTL for live-ish feel)
_cache = {}
_cache_ttl = 5


def get_quote(ticker: str, use_cache: bool = True) -> dict:
    """Fetch single quote with caching."""
    now = time.time()
    print(f"[DEBUG get_quote] now={type(now)}={now}", file=sys.stderr)
    
    if use_cache and ticker in _cache:
        cached_data, cached_time = _cache[ticker]
        print(f"[DEBUG get_quote] cached_time={type(cached_time)}={cached_time}", file=sys.stderr)
        if now - cached_time < _cache_ttl:
            return cached_data
    
    try:
        ticker_obj = yfinance.Ticker(ticker)
        info = ticker_obj.info
        
        # Get historical data for performance calculation
        try:
            hist = ticker_obj.history(period="5y")
            if len(hist) > 0:
                latest_price = hist['Close'].iloc[-1]
                # Calculate returns for each period based on chart timeframes
                from datetime import datetime as dt, timedelta
                now_local = dt.now(hist.index.tz)
                
                # 1D (previous close to now - use yesterday's close)
                prev_close = hist['Close'].iloc[-2] if len(hist) > 1 else latest_price
                ret_1d = ((latest_price / prev_close) - 1) * 100 if prev_close else 0
                
                # 1W (7 days ago)
                one_wk = hist[hist.index >= now_local - timedelta(days=7)]
                ret_1w = ((latest_price / one_wk['Close'].iloc[0]) - 1) * 100 if len(one_wk) > 1 else 0
                
                # 1M (30 days ago)
                one_mo = hist[hist.index >= now_local - timedelta(days=30)]
                ret_1m = ((latest_price / one_mo['Close'].iloc[0]) - 1) * 100 if len(one_mo) > 1 else 0
                
                # 3M (90 days ago)
                three_mo = hist[hist.index >= now_local - timedelta(days=90)]
                ret_3m = ((latest_price / three_mo['Close'].iloc[0]) - 1) * 100 if len(three_mo) > 1 else 0
                
                # YTD performance
                ytd_start = hist[hist.index >= '2026-01-01']
                ret_ytd = ((latest_price / ytd_start['Close'].iloc[0]) - 1) * 100 if len(ytd_start) > 1 else 0
                
                # 1Y performance
                one_yr = hist[hist.index >= '2025-03-31']
                ret_1y = ((latest_price / one_yr['Close'].iloc[0]) - 1) * 100 if len(one_yr) > 1 else 0
                
                # 5Y performance
                five_yr = hist[hist.index >= '2021-03-31']
                ret_5y = ((latest_price / five_yr['Close'].iloc[0]) - 1) * 100 if len(five_yr) > 1 else 0
            else:
                ret_1d = ret_1w = ret_1m = ret_3m = ret_ytd = ret_1y = ret_5y = 0
        except Exception as e:
            print(f"[ERROR get_quote history] {type(e).__name__}: {e}", file=sys.stderr)
            ret_1d = ret_1w = ret_1m = ret_3m = ret_ytd = ret_1y = ret_5y = 0
        
        quote = {
            "ticker": ticker.upper(),
            "name": info.get("shortName", info.get("longName", ticker)),
            "price": info.get("currentPrice", info.get("regularMarketPrice")),
            "change": info.get("regularMarketChange"),
            "change_pct": info.get("regularMarketChangePercent"),
            "open": info.get("regularMarketOpen"),
            "high": info.get("regularMarketDayHigh"),
            "low": info.get("regularMarketDayLow"),
            "volume": info.get("regularMarketVolume"),
            "avg_volume": info.get("averageVolume"),
            "market_cap": info.get("marketCap"),
            "pe_ratio": info.get("trailingPE"),
            "dividend_yield": info.get("dividendYield"),
            "52w_high": info.get("fiftyTwoWeekHigh"),
            "52w_low": info.get("fiftyTwoWeekLow"),
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