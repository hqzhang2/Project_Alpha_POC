"""
Yahoo Finance Options Fetcher with Greeks (KAN-26)
"""
import json
import math
import time
import datetime
from typing import Optional
from functools import partial
import yfinance
import sys
import os

# Add current dir to path for local imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from greeks import calculate_greeks

from scipy.stats import norm
from scipy.optimize import brentq
import numpy as np

# Custom JSON encoder to handle pandas Timestamps and numpy types
class SafeJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if hasattr(obj, 'item'):  # numpy types
            return obj.item()
        if hasattr(obj, 'to_pydatetime'):  # pandas Timestamp
            return obj.to_pydatetime().isoformat()
        return super().default(obj)

def json_dumps(data):
    return json.dumps(data, cls=SafeJSONEncoder)


_options_cache = {}
_cache_ttl = 30

def get_expirations(ticker: str) -> list[str]:
    """Fetch available expiration dates."""
    try:
        return list(yfinance.Ticker(ticker).options)
    except:
        return []


def calculate_implied_volatility(option_price, S, K, T, r, option_type="call"):
    """Calculate implied volatility from option price using Black-Scholes"""
    if option_price <= 0 or T <= 0 or S <= 0 or K <= 0:
        return None
    
    # Check intrinsic value - option price can't be below intrinsic
    if option_type == "call":
        intrinsic = max(0, S - K)
    else:
        intrinsic = max(0, K - S)
    
    # If price is below intrinsic, it's likely stale data - use intrinsic as floor
    if option_price < intrinsic * 0.9:  # Allow 10% buffer
        option_price = intrinsic
    
    # For very short expiry (< 7 days), IV calculation is unreliable due to time decay dominance
    # Use moneyness-based heuristic instead
    if T < 7/365:
        moneyness = S / K
        if option_type == "call":
            if moneyness > 1.1:  # Deep ITM
                return 0.15  # Low IV expected
            elif moneyness < 0.9:  # Deep OTM
                return 0.40  # Higher IV for OTM
            else:
                return 0.25
        else:
            if moneyness < 0.9:  # Deep ITM (put)
                return 0.15
            elif moneyness > 1.1:  # Deep OTM (put)
                return 0.40
            else:
                return 0.25
    
    def black_scholes_price(sigma):
        try:
            d1 = (np.log(S/K) + (r + 0.5*sigma**2)*T) / (sigma*np.sqrt(T))
            d2 = d1 - sigma*np.sqrt(T)
            if option_type == "call":
                price = S*norm.cdf(d1) - K*np.exp(-r*T)*norm.cdf(d2)
            else:
                price = K*np.exp(-r*T)*norm.cdf(-d2) - S*norm.cdf(-d1)
            return price
        except Exception as e:
            return None
    
    try:
        # Use brentq for root finding
        from scipy.optimize import brentq
        result = brentq(
            lambda sig: (black_scholes_price(sig) or 0) - option_price,
            0.001, 2.0
        )
        return result
    except Exception as e:
        # Fallback: use moneyness-based estimate
        moneyness = S / K
        return 0.25 if 0.9 <= moneyness <= 1.1 else 0.20


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
        
        # Clear yfinance internal cache to avoid stale data (check attr first)
        if hasattr(yfinance, 'cache') and hasattr(yfinance.cache, 'clear'):
            yfinance.cache.clear()
        
        # Use first available expiry if none provided
        if not expiry:
            all_expiries = ticker_obj.options
            if not all_expiries:
                return {"ticker": ticker.upper(), "error": "No options available"}
            expiry = all_expiries[0]

        options = ticker_obj.option_chain(expiry)
        
        # Get spot price for Greeks - prefer regularMarketPrice for ETFs
        info = ticker_obj.info
        spot = info.get("regularMarketPrice", info.get("currentPrice"))
        if not spot or spot < 0:
            # Try fast_info for ETF
            try:
                spot = ticker_obj.fast_info.get('last_price')
            except:
                pass
        
        # Calculate Time to Maturity (T)
        expiry_dt = datetime.datetime.strptime(expiry, '%Y-%m-%d')
        # Standardize to end of day
        expiry_dt = expiry_dt.replace(hour=23, minute=59, second=59)
        delta_t = expiry_dt - datetime.datetime.now()
        T = max(0.0001, delta_t.total_seconds() / (365.25 * 24 * 3600))
        
        r = 0.045 # 4.5% risk-free rate proxy

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
        
        def process_df(df, opt_type):
            if df is None: return []
            df = df.rename(columns=rename_map)
            records = df.to_dict('records')
            
            clean_records = []
            for row in records:
                # Clean NaN
                row = {k: (None if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else v) for k, v in row.items()}
                
                # Calculate Greeks - use provided IV or calculate from price
                sigma = row.get('iv', 0)
                if not sigma or sigma < 0.01:
                    # Calculate IV from option price (use 'last' after rename)
                    price = row.get('last') or row.get('bid') or row.get('ask')
                    if price and price > 0:
                        sigma = calculate_implied_volatility(price, spot, row['strike'], T, r, opt_type)
                
                if sigma and sigma > 0.01 and spot and row.get('strike'):
                    try:
                        g = calculate_greeks(spot, row['strike'], T, r, sigma, opt_type)
                        row.update(g)
                        row['iv'] = sigma  # Update with calculated IV
                    except:
                        row.update({'delta': 0, 'gamma': 0, 'theta': 0, 'vega': 0, 'rho': 0})
                else:
                    row.update({'delta': 0, 'gamma': 0, 'theta': 0, 'vega': 0, 'rho': 0})
                
                clean_records.append(row)
            return clean_records

        result = {
            "ticker": ticker.upper(),
            "expiry": expiry,
            "spot": spot,
            "calls": process_df(calls, 'call'),
            "puts": process_df(puts, 'put'),
            "timestamp": now
        }
        
        _options_cache[cache_key] = (result, now)
        return result
        
    except Exception as e:
        return {"ticker": ticker.upper(), "error": str(e)}

def get_options_json(ticker: str) -> str:
    """Return options as JSON string."""
    return json.dumps(get_options_chain(ticker))

if __name__ == "__main__":
    # Test
    print(get_options_json("SPY"))
