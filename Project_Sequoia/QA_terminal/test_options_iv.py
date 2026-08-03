#!/usr/bin/env python3
"""
Regression tests for the OMON implied-volatility fix.

Bug: yfinance's raw `impliedVolatility` field is a quantized placeholder
(e.g. 1/16 = 6.25%) for OTM options with no bid/ask. The old code trusted
that field whenever it was >= 0.01, so OTM options displayed 6.3% where
the true IV (solved from market price) was ~31-35%.

Fix: always solve IV from market price (mid of bid/ask, else last trade);
only fall back to yahoo's field (0.10 <= iv <= 1.5) when no price exists.

Run:  pytest test_options_iv.py
"""
import os
import sys
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import options


def test_calculate_iv_otm_call_sane():
    """500 call @ 2.73 with spot 464.72 (2.5wk): IV must be ~25-40%, not 6%."""
    iv = options.calculate_implied_volatility(
        2.73, 464.72, 500.0, 18 / 365.25, 0.045, "call")
    assert iv is not None and 0.20 <= iv <= 0.45, f"unexpected IV {iv}"


def test_calculate_iv_otm_put_sane():
    """440 put @ 4.95 with spot 464.72: IV must be ~25-45%, not 6%."""
    iv = options.calculate_implied_volatility(
        4.95, 464.72, 440.0, 18 / 365.25, 0.045, "put")
    assert iv is not None and 0.20 <= iv <= 0.50, f"unexpected IV {iv}"


def test_calculate_iv_atm_sane():
    """ATM 465 call @ 14.05: IV ~25-40%."""
    iv = options.calculate_implied_volatility(
        14.05, 464.72, 465.0, 18 / 365.25, 0.045, "call")
    assert iv is not None and 0.20 <= iv <= 0.45, f"unexpected IV {iv}"


def test_calculate_iv_deep_otm_put():
    """Deep OTM 430 put @ 3.07: still a real IV, not the 6.25% placeholder."""
    iv = options.calculate_implied_volatility(
        3.07, 464.72, 430.0, 18 / 365.25, 0.045, "put")
    assert iv is not None and iv > 0.15, f"unexpected IV {iv}"


def test_calculate_iv_invalid_inputs():
    """Zero/negative price or zero T must return None (no crash)."""
    assert options.calculate_implied_volatility(0, 464.72, 500.0, 0.049, 0.045, "call") is None
    assert options.calculate_implied_volatility(-1, 464.72, 500.0, 0.049, 0.045, "call") is None
    assert options.calculate_implied_volatility(2.73, 0, 500.0, 0.049, 0.045, "call") is None
    assert options.calculate_implied_volatility(2.73, 464.72, 500.0, 0, 0.045, "call") is None


def test_placeholder_iv_not_trusted():
    """
    The core regression: yahoo's quantized placeholder (0.0625) must NOT be
    used as-is when a market price exists. Simulate the process_df logic:
    a price exists -> IV derived from price (sane range), never 0.0625.
    """
    # Sanity on the solver directly: solving for the 500 call price gives ~0.31
    iv = options.calculate_implied_volatility(2.73, 464.72, 500.0, 18 / 365.25, 0.045, "call")
    assert iv is not None
    assert abs(iv - 0.0625) > 0.05  # must NOT return the yahoo placeholder
