"""
Yahoo Finance Options Fetcher
KAN-23: Yahoo Finance Options Data
"""
import json
import math
import time
from typing import Optional
import yfinance


_options_cache = {}
_cache_ttl = 30


def get_expirations(ticker: str) -> list[str]:
    """Fetch available expiration dates."""
    try:
        return list(yfinance.Ticker(ticker).options)
    except:
        return []


def get_options_chain(ticker: str, expiry: str = None, use_cache: bool = True) -> dict:
    """Fetch options chain for a ticker and specific expiry."""
    now = time.time()
    
    cache_key = f"options_{ticker}_{expiry}"
    if use_cache and cache_key in _options_cache:
        cached_data, cached_time = _options_cache[cache_key]
        if now - cached_time < _cache_ttl:
            return cached_data
    
    try:
        ticker_obj = yfinance.Ticker(ticker)
        
        # Use first available expiry if none provided
        if not expiry:
            all_expiries = ticker_obj.options
            if not all_expiries:
                return {"ticker": ticker.upper(), "error": "No options available"}
            expiry = all_expiries[0]

        options = ticker_obj.option_chain(expiry)
        
        if not options:
            return {"ticker": ticker.upper(), "error": "No options data"}
        
        # Get expiry dates from options
        # yfinance returns calls/puts directly
        calls = options.calls.copy() if options.calls is not None else None
        puts = options.puts.copy() if options.puts is not None else None
        
        # Rename columns to standard names
        rename_map = {
            'contractSymbol': 'symbol',
            'strike': 'strike',
            'lastPrice': 'last',
            'bid': 'bid',
            'ask': 'ask',
            'volume': 'vol',
            'openInterest': 'oi',
            'impliedVolatility': 'iv',
            'inTheMoney': 'itm'
        }
        
        if calls is not None:
            calls = calls.rename(columns=rename_map)
            calls = calls[['strike', 'bid', 'ask', 'last', 'vol', 'oi', 'iv', 'itm']]
        
        if puts is not None:
            puts = puts.rename(columns=rename_map)
            puts = puts[['strike', 'bid', 'ask', 'last', 'vol', 'oi', 'iv', 'itm']]
        
        # Clean NaN values
        def clean_val(v):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                return None
            return v
        
        calls_clean = []
        if calls is not None:
            for row in calls.to_dict('records'):
                calls_clean.append({k: clean_val(v) for k, v in row.items()})
        
        puts_clean = []
        if puts is not None:
            for row in puts.to_dict('records'):
                puts_clean.append({k: clean_val(v) for k, v in row.items()})
        
        result = {
            "ticker": ticker.upper(),
            "calls": calls_clean,
            "puts": puts_clean,
            "timestamp": now
        }
        
        _options_cache[ticker] = (result, now)
        return result
        
    except Exception as e:
        return {"ticker": ticker.upper(), "error": str(e)}


def get_options_json(ticker: str) -> str:
    """Return options as JSON string."""
    return json.dumps(get_options_chain(ticker))


if __name__ == "__main__":
    # Test
    print(get_options_json("SPY"))