#!/usr/bin/env python3
"""
Common Indicators Library
Unified technical analysis indicators for all projects.
"""
import warnings
from typing import Optional

import numpy as np
import pandas as pd

# Try to import hmmlearn
try:
    from hmmlearn.hmm import GaussianHMM
    HMM_AVAILABLE = True
except ImportError:
    HMM_AVAILABLE = False
    GaussianHMM = None

warnings.filterwarnings("ignore", category=RuntimeWarning)


# ============================================================================
# Moving Averages
# ============================================================================

def sma(series: pd.Series, window: int) -> pd.Series:
    """Simple Moving Average."""
    return series.rolling(window=window, min_periods=window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


# ============================================================================
# Momentum Indicators
# ============================================================================

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    Relative Strength Index (Wilder's smoothing).
    """
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    MACD: returns (macd_line, signal_line, histogram)
    """
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def stoch(series: pd.Series, high: pd.Series, low: pd.Series, period: int = 14, smooth: int = 3) -> tuple[pd.Series, pd.Series]:
    """Stochastic Oscillator: returns (%K, %D)"""
    lowest_low = low.rolling(window=period).min()
    highest_high = high.rolling(window=period).max()
    k = 100 * (series - lowest_low) / (highest_high - lowest_low).replace(0, np.nan)
    d = k.rolling(window=smooth).mean()
    return k, d


# ============================================================================
# Trend Indicators
# ============================================================================

def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """
    Average Directional Index (Wilder's smoothing).
    """
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)

    dm_plus = (high - high.shift()).clip(lower=0)
    dm_minus = (low.shift() - low).clip(lower=0)

    dm_plus = dm_plus.where(dm_plus > dm_minus, 0)
    dm_minus = dm_minus.where(dm_minus > dm_plus, 0)

    atr = tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    di_plus = 100 * dm_plus.ewm(alpha=1/period, adjust=False, min_periods=period).mean() / atr.replace(0, np.nan)
    di_minus = 100 * dm_minus.ewm(alpha=1/period, adjust=False, min_periods=period).mean() / atr.replace(0, np.nan)

    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
    return dx.ewm(alpha=1/period, adjust=False, min_periods=period).mean()


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range."""
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()


def bollinger_bands(series: pd.Series, window: int = 20, num_std: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (middle, upper, lower)"""
    middle = sma(series, window)
    std = series.rolling(window=window, min_periods=window).std()
    upper = middle + num_std * std
    lower = middle - num_std * std
    return middle, upper, lower


def bb_position(series: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.Series:
    """Bollinger Band position (0 = lower band, 1 = upper band)."""
    middle, upper, lower = bollinger_bands(series, window, num_std)
    return (series - lower) / (upper - lower).replace(0, np.nan)


# ============================================================================
# Volume Indicators
# ============================================================================

def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On Balance Volume."""
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume).cumsum()


def obv_slope(obv_series: pd.Series, window: int = 20) -> float:
    """Linear regression slope of OBV over window."""
    tail = obv_series.dropna().tail(window)
    if len(tail) < 4:
        return 0.0
    slope, _ = np.polyfit(range(len(tail)), tail.values, 1)
    return float(slope)


# ============================================================================
# HMM Regime Detection
# ============================================================================

def fit_hmm(close: pd.Series, n_states: int = 2, n_iter: int = 500, random_state: int = 42) -> tuple[int, float, list[float]]:
    """
    Fit Hidden Markov Model to log returns for regime detection.
    Returns: (bull_state_idx, current_bull_prob, all_bull_probs)
    """
    if not HMM_AVAILABLE:
        return 0, 0.5, [0.5] * len(close)

    returns = np.log(close / close.shift(1)).dropna().values.reshape(-1, 1)
    if len(returns) < 20:
        return 0, 0.5, [0.5] * len(close)

    model = GaussianHMM(
        n_components=n_states,
        covariance_type="full",
        n_iter=n_iter,
        random_state=random_state
    )
    model.fit(returns)

    posteriors = model.predict_proba(returns)
    means = [model.means_[s][0] for s in range(n_states)]
    bull_state = int(np.argmax(means))
    current_bull_prob = float(posteriors[-1, bull_state])
    bull_probs = posteriors[:, bull_state].tolist()

    return bull_state, current_bull_prob, bull_probs


# ============================================================================
# Compound / Utility
# ============================================================================

def compute_all(close: pd.Series, high: pd.Series = None, low: pd.Series = None,
                volume: pd.Series = None, **kwargs) -> dict:
    """
    Compute all indicators at once for efficiency.
    Returns dict of Series.
    """
    result = {}

    # Moving averages
    for w in [10, 20, 50, 100, 200]:
        result[f'sma_{w}'] = sma(close, w)
        result[f'ema_{w}'] = ema(close, w)

    # MACD
    macd_line, signal_line, hist = macd(close)
    result['macd'] = macd_line
    result['macd_signal'] = signal_line
    result['macd_hist'] = hist

    # RSI
    result['rsi'] = rsi(close)

    # Bollinger Bands
    middle, upper, lower = bollinger_bands(close)
    result['bb_middle'] = middle
    result['bb_upper'] = upper
    result['bb_lower'] = lower
    result['bb_position'] = bb_position(close)

    # ADX / ATR if OHLC provided
    if high is not None and low is not None:
        result['adx'] = adx(high, low, close)
        result['atr'] = atr(high, low, close)

    # OBV if volume provided
    if volume is not None:
        result['obv'] = obv(close, volume)

    return result


# ============================================================================
# Validation / Testing
# ============================================================================

if __name__ == "__main__":
    # Quick sanity check with synthetic data
    np.random.seed(42)
    dates = pd.date_range('2024-01-01', periods=100, freq='D')
    prices = 100 + np.cumsum(np.random.randn(100) * 0.5)
    close = pd.Series(prices, index=dates)
    high = close + np.abs(np.random.randn(100) * 0.2)
    low = close - np.abs(np.random.randn(100) * 0.2)
    volume = pd.Series(np.random.randint(1000000, 10000000, 100), index=dates)

    print("Testing indicators...")
    print(f"RSI(14) last: {rsi(close).iloc[-1]:.2f}")
    macd_l, sig, hist = macd(close)
    print(f"MACD last: {macd_l.iloc[-1]:.4f}, Signal: {sig.iloc[-1]:.4f}, Hist: {hist.iloc[-1]:.4f}")
    mid, up, lo = bollinger_bands(close)
    print(f"BB Position: {bb_position(close).iloc[-1]:.4f}")
    print(f"ADX last: {adx(high, low, close).iloc[-1]:.2f}")
    print(f"OBV slope: {obv_slope(obv(close, volume)):.2f}")

    if HMM_AVAILABLE:
        bull_state, bull_prob, _ = fit_hmm(close)
        print(f"HMM: bull_state={bull_state}, bull_prob={bull_prob:.4f}")
    else:
        print("HMM: not available (hmmlearn not installed)")

    print("All indicators computed successfully!")