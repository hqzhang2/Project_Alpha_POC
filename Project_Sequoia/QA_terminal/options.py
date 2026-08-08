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
from vollib.black_scholes_merton.greeks.analytical import d2 as _vollib_d2
from vollib.black_scholes_merton.greeks.analytical import N as _norm_cdf


def probability_itm(flag, S, K, T, r, sigma, q=0.0):
    """
    Probability of finishing in-the-money at expiry (risk-neutral, BS):
        call: N(d2) ; put: N(-d2)
    """
    if sigma is None or sigma <= 0 or T <= 0:
        return None
    try:
        d2_val = _vollib_d2(S, K, T, r, sigma, q)
        return float(_norm_cdf(d2_val) if flag == 'c' else _norm_cdf(-d2_val))
    except Exception:
        return None

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


def _mid_or_last(row):
    """
    Best available price for an option row: bid/ask mid when a two-sided
    market exists, else the last trade. Returns None when neither exists.
    """
    bid, ask, last = row.get('bid'), row.get('ask'), row.get('last')
    if bid and ask and bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    if last and last > 0:
        return last
    return None


def implied_forward(call_row, put_row, K, T, r):
    """
    Forward price implied by put-call parity at one strike:
        F = C_mid - P_mid + K*exp(-rT)
    Returns None when either side lacks a usable price.
    """
    c_price = _mid_or_last(call_row)
    p_price = _mid_or_last(put_row)
    if c_price is None or p_price is None:
        return None
    return c_price - p_price + K * math.exp(-r * T)


def parity_residual(fwd, fwd_median, call_row, put_row, min_floor=0.0):
    """
    Per-strike parity anomaly vs the chain's median implied forward (dollars).
    Positive means the strike's forward is rich vs the chain consensus.

    Using the median forward (not the spot) removes the systematic
    carry/dividend offset that otherwise flags every strike; genuine bad
    quotes deviate from the consensus and get caught.

    min_floor: absolute dollar floor (e.g. 0.25% of spot) so stale-quote
    noise in the liquid ATM region doesn't over-trigger; only residuals
    beyond BOTH the spread-based floor and min_floor flag as violations.
    """
    if fwd is None or fwd_median is None:
        return None, None
    residual = fwd - fwd_median
    # Noise floor: half the combined bid/ask spread + 5c, so stale quotes
    # don't trip the flag but real mispricings do.
    c_spread = (call_row.get('ask') or 0) - (call_row.get('bid') or 0)
    p_spread = (put_row.get('ask') or 0) - (put_row.get('bid') or 0)
    floor = max(min_floor, 0.05, (max(c_spread, 0) + max(p_spread, 0)) / 2 + 0.05)
    return residual, abs(residual) <= floor


_options_cache = {}
_cache_ttl = 30

def get_expirations(ticker: str) -> list[str]:
    """Fetch available expiration dates."""
    try:
        return list(yfinance.Ticker(ticker).options)
    except:
        return []


def calculate_implied_volatility(option_price, S, K, T, r, option_type="call", q=0.0):
    """
    Calculate implied volatility from option price using vollib's
    Let's Be Rational engine (Black-Scholes-Merton with dividend yield q).
    """
    if option_price <= 0 or T <= 0 or S <= 0 or K <= 0:
        return None

    # Check intrinsic value - option price can't be below intrinsic
    if option_type == "call":
        intrinsic = max(0, S - K)
    else:
        intrinsic = max(0, K - S)

    # If price is below intrinsic, it's likely stale data - use intrinsic as
    # floor (plus epsilon so vollib's solver stays well-defined).
    if option_price < intrinsic:
        option_price = intrinsic + 1e-8

    try:
        from vollib.black_scholes_merton.implied_volatility import implied_volatility
        flag = 'c' if option_type == 'call' else 'p'
        return implied_volatility(option_price, S, K, T, r, q, flag)
    except Exception:
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

        # Continuous dividend yield (decimal) for BSM pricing.
        # CAUTION: yfinance's 'dividendYield' is a PERCENTAGE (0.78 = 0.78%),
        # not a decimal — feeding 0.78 as q would imply a 78% yield and
        # explode the IV solver. 'trailingAnnualDividendYield' is the proper
        # decimal; fall back to dividendYield/100, with a sanity clamp.
        q = info.get("trailingAnnualDividendYield")
        if q is None:
            q = (info.get("dividendYield") or 0.0) / 100.0
        if not isinstance(q, (int, float)) or not (0.0 <= q <= 0.20):  # max 20% yield
            q = 0.0
        
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
                
                # Calculate Greeks. IV is ALWAYS derived from market price (mid or
                # last) - yfinance's raw impliedVolatility field is a quantized
                # placeholder (e.g. 1/16 = 6.25%) for OTM options with no bid/ask,
                # NOT a real IV. Trusting it displayed 6.3% where true IV was ~32%.
                price = _mid_or_last(row)

                # Liquidity flags: a two-sided quote is a real market; anything
                # else (last-only or nothing) is stale/illiquid and gets dimmed.
                bid, ask = row.get('bid'), row.get('ask')
                has_quote = bool(bid and ask and bid > 0 and ask > 0)
                row['hasQuote'] = has_quote
                row['spread'] = round(ask - bid, 2) if has_quote else None
                row['spreadPct'] = round((ask - bid) / ((bid + ask) / 2) * 100, 2) if has_quote else None
                row['illiquid'] = not has_quote

                sigma = None
                if price and spot and row.get('strike'):
                    sigma = calculate_implied_volatility(price, spot, row['strike'], T, r, opt_type, q)
                if not sigma or sigma < 0.01:
                    # No usable market price -> fall back to yahoo's field, but
                    # only if it looks like a real IV. Yahoo's placeholders for
                    # untraded options are quantized <= 6.25%; real equity IVs
                    # are >= ~10%. Leave None otherwise (page shows '-').
                    y_iv = row.get('iv', 0)
                    sigma = y_iv if (y_iv and 0.10 <= y_iv <= 1.5) else None
                
                if sigma and sigma > 0.01 and spot and row.get('strike'):
                    try:
                        g = calculate_greeks(spot, row['strike'], T, r, sigma, opt_type, q)
                        row.update(g)
                        row['iv'] = sigma  # Update with calculated IV
                    except:
                        row.update({'delta': 0, 'gamma': 0, 'theta': 0, 'vega': 0, 'rho': 0})
                else:
                    row.update({'delta': 0, 'gamma': 0, 'theta': 0, 'vega': 0, 'rho': 0})

                # Probability of finishing ITM (risk-neutral, N(d2)/N(-d2)).
                # Only meaningful when we have a real IV.
                if sigma and sigma > 0.01 and spot and row.get('strike'):
                    row['probITM'] = probability_itm('c' if opt_type == 'call' else 'p',
                                                     spot, row['strike'], T, r, sigma, q)
                else:
                    row['probITM'] = None

                clean_records.append(row)
            return clean_records

        calls_processed = process_df(calls, 'call')
        puts_processed = process_df(puts, 'put')

        # Put-call parity per strike: compute each strike's implied forward,
        # then flag anomalies vs the chain's MEDIAN forward. Median (not spot)
        # self-calibrates against carry/dividend offsets so genuine bad
        # quotes stand out instead of every strike flagging.
        call_by_strike = {c['strike']: c for c in calls_processed}
        put_by_strike = {p['strike']: p for p in puts_processed}
        forwards = {}
        for K in set(call_by_strike) & set(put_by_strike):
            f = implied_forward(call_by_strike[K], put_by_strike[K], K, T, r)
            if f is not None:
                forwards[K] = f
        fwd_median = sorted(forwards.values())[len(forwards) // 2] if forwards else None
        # min_floor = 0.25% of spot: stale-quote noise in the liquid ATM
        # region (~$0.3-1.0 residual) must not flag; genuine bad quotes
        # (deep-OTM wings, $10-100+) still do.
        min_floor = 0.0025 * spot if spot else 0.0
        for K, fwd in forwards.items():
            residual, ok = parity_residual(fwd, fwd_median, call_by_strike[K], put_by_strike[K], min_floor)
            if residual is not None:
                call_by_strike[K]['parityResidual'] = round(residual, 3)
                call_by_strike[K]['parityOk'] = ok
                call_by_strike[K]['impliedForward'] = round(fwd, 3)
                put_by_strike[K]['parityResidual'] = round(residual, 3)
                put_by_strike[K]['parityOk'] = ok
                put_by_strike[K]['impliedForward'] = round(fwd, 3)

        # Expected move to expiry: ATM straddle (call mid + put mid at the
        # strike nearest spot). Market-implied, uses the same mids as IV.
        # Only strikes present on BOTH sides qualify — call/put grids can
        # differ (live: IJH ATM call 78.0 had no matching put -> KeyError
        # killed the whole chain). Guard with the shared-strike set.
        expected_move = None
        if call_by_strike and put_by_strike and spot:
            shared = set(call_by_strike) & set(put_by_strike)
            if shared:
                atm_strike = min(shared, key=lambda k: abs(k - spot))
                c_atm, p_atm = call_by_strike[atm_strike], put_by_strike[atm_strike]
                c_mid = _mid_or_last(c_atm)
                p_mid = _mid_or_last(p_atm)
                if c_mid is not None and p_mid is not None and c_mid > 0 and p_mid > 0:
                    expected_move = {
                        'strike': atm_strike,
                        'straddle': round(c_mid + p_mid, 2),
                        'pct': round((c_mid + p_mid) / spot * 100, 2)
                    }

        result = {
            "ticker": ticker.upper(),
            "expiry": expiry,
            "spot": spot,
            "dividendYield": round(q, 4) if q else None,
            "medianForward": round(fwd_median, 3) if fwd_median else None,
            "expectedMove": expected_move,
            "calls": calls_processed,
            "puts": puts_processed,
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
