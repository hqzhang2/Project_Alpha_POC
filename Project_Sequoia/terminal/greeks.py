"""
Black-Scholes-Merton Greeks via vollib (Jaeckel's Let's Be Rational engine).

Replaces the hand-rolled Black-Scholes implementation with the maintained
vollib library (already used by Project_Nine_Street/scripts/nsoe_pricing.py),
keeping the dashboard's display conventions:
  - theta: per-day (raw BS theta is per-year, divided by 365)
  - vega:  per 1% vol move (raw is per 100%, divided by 100)
  - rho:   per 1% rate move (raw is per 100%, divided by 100)
"""
from vollib.black_scholes_merton.greeks.analytical import delta, gamma, theta, vega, rho


def calculate_greeks(S, K, T, r, sigma, option_type='call', q=0.0):
    """
    Calculate Black-Scholes-Merton Greeks.

    S: Spot Price
    K: Strike Price
    T: Time to Maturity (years)
    r: Risk-free rate (decimal)
    sigma: Volatility (decimal)
    option_type: 'call' or 'put'
    q: continuous dividend yield (decimal, 0 if none)
    """
    if T <= 0 or sigma <= 0:
        return {
            'delta': 0.0,
            'gamma': 0.0,
            'theta': 0.0,
            'vega': 0.0,
            'rho': 0.0
        }

    flag = 'c' if option_type == 'call' else 'p'

    return {
        'delta': float(delta(flag, S, K, T, r, sigma, q)),
        'gamma': float(gamma(flag, S, K, T, r, sigma, q)),
        'theta': float(theta(flag, S, K, T, r, sigma, q) / 365.0),
        'vega': float(vega(flag, S, K, T, r, sigma, q) / 100.0),
        'rho': float(rho(flag, S, K, T, r, sigma, q) / 100.0)
    }
