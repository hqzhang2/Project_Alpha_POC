"""
Yahoo Finance Quote Fetcher
KAN-19: Yahoo Finance Quote Integration
"""
import json
import time
from typing import Optional
import yfinance

# Cache for quotes (5s TTL for live-ish feel)
_cache = {}
_cache_ttl = 5


def get_quote(ticker: str, use_cache: bool = True) -> dict:
    """Fetch single quote with caching."""
    now = time.time()
    
    if use_cache and ticker in _cache:
        cached_data, cached_time = _cache[ticker]
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
                # YTD performance
                ytd_start = hist[hist.index >= '2026-01-01']
                ytd_return = ((latest_price / ytd_start['Close'].iloc[0]) - 1) * 100 if len(ytd_start) > 1 else 0
                # 1Y performance
                one_yr = hist[hist.index >= '2025-03-31']
                one_yr_return = ((latest_price / one_yr['Close'].iloc[0]) - 1) * 100 if len(one_yr) > 1 else 0
                # 5Y performance
                five_yr = hist[hist.index >= '2021-03-31']
                five_yr_return = ((latest_price / five_yr['Close'].iloc[0]) - 1) * 100 if len(five_yr) > 1 else 0
            else:
                ytd_return = one_yr_return = five_yr_return = 0
        except:
            ytd_return = one_yr_return = five_yr_return = 0
        
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
            "ytd_return": ytd_return,
            "1yr_return": one_yr_return,
            "5yr_return": five_yr_return,
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
        results[ticker.upper()] = get_quote(ticker, use_cache)
        time.sleep(0.1)  # Rate limiting
    return results


def get_quotes_json(tickers: list[str]) -> str:
    """Return quotes as JSON string."""
    return json.dumps(get_quotes(tickers))


if __name__ == "__main__":
    # Test
    test_tickers = ["SPY", "QQQ", "TLT"]
    print(get_quotes_json(test_tickers))