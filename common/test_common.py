#!/usr/bin/env python3
"""
Unit tests for the shared `common/` library (indicators + risk).

These are pure, deterministic, network-free tests. They protect the
highest-blast-radius code in the repo: a regression in `common/risk` or
`common/indicators` propagates to every project that imports them.

Run:  pytest common/test_common.py
"""
import sys
import os
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(__file__))

from indicators import (  # noqa: E402
    sma, ema, rsi, macd, stoch, adx, atr,
    bollinger_bands, bb_position, obv, obv_slope, fit_hmm,
)
from risk import (  # noqa: E402
    sharpe_ratio, sortino_ratio, max_drawdown, volatility,
    beta, alpha, var, cvar, risk_parity_weights,
    equal_weight_returns, kelly_fraction, position_size_kelly,
    position_size_vol_target,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def close():
    # 30 monotonically increasing prices -> RSI should be ~100 (all gains)
    return pd.Series(np.arange(1, 31, dtype=float))


@pytest.fixture
def ohlc():
    idx = pd.date_range("2024-01-01", periods=30, freq="D")
    close = pd.Series(np.arange(1, 31, dtype=float), index=idx)
    high = close + 1.0
    low = close - 1.0
    vol = pd.Series(np.full(30, 1000), index=idx)
    return close, high, low, vol


@pytest.fixture
def returns():
    # deterministic small return series
    rng = np.random.RandomState(7)
    return pd.Series(rng.randn(252) * 0.01 + 0.0005)


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------
def test_sma_basic():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    out = sma(s, 3)
    assert out.iloc[2] == pytest.approx(2.0)
    assert np.isnan(out.iloc[1])  # needs `window` periods


def test_ema_not_nan_after_span():
    s = pd.Series(np.arange(1, 31, dtype=float))
    out = ema(s, 5)
    assert not np.isnan(out.iloc[-1])
    # EMA of a ramp is above the last value's simple average trend
    assert out.iloc[-1] > 15


def test_rsi_all_gains_is_100(close):
    out = rsi(close, period=14)
    assert out.iloc[-1] == pytest.approx(100.0)


def test_rsi_all_losses_is_0():
    s = pd.Series(np.arange(30, 0, -1, dtype=float))  # strictly decreasing
    out = rsi(s, period=14)
    assert out.iloc[-1] == pytest.approx(0.0)


def test_rsi_bounded(close):
    out = rsi(close, period=14)
    assert out.dropna().between(0, 100).all()


def test_macd_shape_and_hist():
    s = pd.Series(np.arange(1, 61, dtype=float))
    macd_line, signal_line, hist = macd(s)
    assert len(macd_line) == len(signal_line) == len(hist)
    assert np.allclose(hist.dropna(), (macd_line - signal_line).dropna())


def test_bollinger_bands_order():
    s = pd.Series(np.arange(1, 61, dtype=float))
    mid, up, lo = bollinger_bands(s, window=20, num_std=2.0)
    # upper >= middle >= lower where defined
    valid = ~up.isna()
    assert (up[valid] >= mid[valid]).all()
    assert (mid[valid] >= lo[valid]).all()


def test_bb_position_bounds():
    s = pd.Series(np.arange(1, 61, dtype=float))
    pos = bb_position(s)
    assert pos.dropna().between(-1, 2).all()  # can exceed [0,1] briefly at edges


def test_stoch_bounds():
    close, high, low, _ = [pd.Series(np.arange(1, 31, dtype=float)) for _ in range(4)]
    high = close + 1
    low = close - 1
    k, d = stoch(close, high, low, period=14, smooth=3)
    assert k.dropna().between(0, 100).all()


def test_adx_atr_positive(ohlc):
    close, high, low, _ = ohlc
    a = atr(high, low, close, period=14)
    assert (a.dropna() > 0).all()
    adx_v = adx(high, low, close, period=14)
    assert adx_v.dropna().between(0, 100).all()


def test_obv_cumulative(ohlc):
    close, high, low, vol = ohlc
    out = obv(close, vol)
    # OBV is the cumulative sum of signed volume
    expected = (np.sign(close.diff().fillna(0)) * vol).cumsum()
    assert out.iloc[-1] == pytest.approx(expected.iloc[-1])


def test_obv_slope_runs():
    close, high, low, vol = [pd.Series(np.arange(1, 31, dtype=float)) for _ in range(4)]
    out = obv(close, vol)
    # slope runs without error and returns a float
    assert isinstance(obv_slope(out, window=20), float)


def test_fit_hmm_runs_and_returns_probs():
    # Works whether or not hmmlearn is installed (graceful fallback)
    s = pd.Series(np.arange(1, 31, dtype=float))
    bull_state, bull_prob, probs = fit_hmm(s, n_states=2)
    assert 0.0 <= bull_prob <= 1.0
    # probs align to the log-return window (one fewer than the price series)
    assert 0 < len(probs) <= len(s)


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------
def test_sharpe_zero_for_constant_returns():
    s = pd.Series(np.full(50, 0.001))
    # zero volatility -> std()==0 -> function returns nan (degenerate input)
    val = sharpe_ratio(s)
    assert np.isnan(val)


def test_sharpe_positive_for_uptrend(returns):
    val = sharpe_ratio(returns)
    assert np.isfinite(val)
    assert val > 0


def test_sortino_finite(returns):
    val = sortino_ratio(returns)
    assert np.isfinite(val)


def test_max_drawdown_known():
    prices = pd.Series([100, 120, 60, 90])  # drops from 120 to 60 => -50%
    assert max_drawdown(prices) == pytest.approx(-50.0)


def test_volatility_annualized(returns):
    daily = volatility(returns, annualize=False)
    ann = volatility(returns, annualize=True)
    assert ann == pytest.approx(daily * np.sqrt(252))


def test_beta_against_self_is_one(returns):
    assert beta(returns, returns) == pytest.approx(1.0)


def test_beta_zero_when_market_constant(returns):
    flat = pd.Series(np.full(len(returns), 0.001))
    flat.index = returns.index
    assert beta(returns, flat) == 0.0


def test_alpha_zero_for_identical(returns):
    assert alpha(returns, returns) == pytest.approx(0.0, abs=1e-9)


def test_var_bounds(returns):
    v = var(returns, confidence=0.95)
    assert np.isfinite(v)


def test_cvar_not_above_var(returns):
    v = var(returns, confidence=0.95)
    c = cvar(returns, confidence=0.95)
    # CVaR (expected shortfall) is at least as bad as VaR
    assert c <= v + 1e-9


def test_risk_parity_weights_sum_to_one():
    cov = pd.DataFrame(
        [[0.04, 0.01], [0.01, 0.09]],
        index=["a", "b"], columns=["a", "b"],
    )
    w = risk_parity_weights(cov)
    assert w.sum() == pytest.approx(1.0)
    assert (w > 0).all()


def test_equal_weight_returns():
    a = pd.Series([0.01, 0.02, 0.03])
    b = pd.Series([0.02, 0.01, 0.04])
    out = equal_weight_returns({"a": a, "b": b})
    assert len(out) == 3
    assert out.iloc[0] == pytest.approx(0.015)


def test_kelly_fraction_valid():
    # win_rate 0.6, win/loss ratio 2 -> positive fraction
    f = kelly_fraction(0.6, 2.0)
    assert 0.0 < f <= 1.0
    # pathological: zero win/loss ratio -> 0
    assert kelly_fraction(0.6, 0.0) == 0.0


def test_position_size_kelly_capped():
    size = position_size_kelly(100000, 0.6, 2.0, max_fraction=0.25)
    assert 0.0 <= size <= 25000.0 + 1e-9


def test_position_size_vol_target_zero_asset_vol():
    assert position_size_vol_target(100000, 0.1, 0.0) == 0.0


def test_position_size_vol_target_leverage_capped():
    size = position_size_vol_target(100000, 0.1, 0.05, max_leverage=1.0)
    assert size <= 100000.0 + 1e-9
