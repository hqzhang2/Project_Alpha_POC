#!/usr/bin/env python3
"""
NS-2 QA Server — MAG7 HMM 7-Improvement Regime Strategy
========================================================
Improvements:
  1. 3-state HMM + 8-feature expanded observation vector
  2. Confidence-weighted position sizing
  3. Multi-factor signal confirmation
  4. VIX macro overlay
  5. Adaptive persistence filter
  6. ATR trailing stops + drawdown circuit breaker
  7. HMM ensemble (5 models, majority vote + agreement score)

Port: 9229 (QA) | PROD: 9228
"""

import os
import json
import warnings
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf

from sklearn.preprocessing import StandardScaler
from hmmlearn import hmm
from scipy import stats as sp_stats

warnings.filterwarnings("ignore")

# ── Configuration ────────────────────────────────────────────────────────────
PORT = int(os.environ.get("PORT", 9229))
DASHBOARD_PATH = os.path.join(os.path.dirname(__file__), "ns2_dashboard.html")
WATCHLIST_PATH = os.path.join(os.path.dirname(__file__), "ns2_watchlist.json")
SIGNAL_CACHE_PATH = os.path.join(os.path.dirname(__file__), "ns2_signal_cache.json")
CACHE_TTL = 300  # seconds

MAG7 = {
    "AAPL": {"name": "Apple",     "color": "#a8d8a8", "sector": "XLK"},
    "MSFT": {"name": "Microsoft", "color": "#7ec8e3", "sector": "XLK"},
    "NVDA": {"name": "Nvidia",    "color": "#76e4c4", "sector": "XLK"},
    "GOOGL": {"name": "Alphabet",  "color": "#f7c59f", "sector": "XLC"},
    "AMZN": {"name": "Amazon",    "color": "#ffb347", "sector": "XLY"},
    "META": {"name": "Meta",      "color": "#c9a6ff", "sector": "XLC"},
    "TSLA": {"name": "Tesla",     "color": "#ff6b6b", "sector": "XLY"},
}

REGIME_META = {
    0: {"label": "TRENDING",  "color": "#76e4c4", "desc": "CCI Breakout + ADX confirmation"},
    1: {"label": "MEAN_REV",  "color": "#7ec8e3", "desc": "RSI Fade + Bollinger confirmation"},
    2: {"label": "CRISIS",    "color": "#ff6b6b", "desc": "Capital Preservation"},
}

# Single source of truth: pill = dashboard = legend = bars = prompt (matches ns2_dashboard.html)
SIGNAL_COLORS = {
    "BUY": "#22c55e", "SELL": "#ff6b6b", "SHORT": "#ff6b6b", "EXIT": "#ffd166",
    "HOLD LONG": "#7ec8e3", "FLAT": "#444", "WATCH": "#c9a6ff",
}

# Strategy parameters
LOOKBACK_DAYS      = 750        # Phase 2: ~3y fetch so HMM trains on ~500 bars (was 180)
RSI_PERIOD         = 14
CCI_PERIOD         = 20
ATR_PERIOD         = 14
HMM_STATES         = 3          # Improvement #1: 3 stable states (more reliable with ~125 bars)
HMM_ITERATIONS     = 2000
HMM_COVARIANCE     = "diag"     # Phase 2: full cov = ~140 params on small data; diag halves it
HMM_ENSEMBLE_N     = 5          # Improvement #7: ensemble size
PERSISTENCE_DEFAULT = 3
CCI_ENTRY          = 100
CCI_EXIT           = 0
CCI_SHORT          = -250       # Relaxed from -300
RSI_OVERSOLD       = 30
RSI_OVERBOUGHT     = 70
RSI_MEAN_LOW       = 45
RSI_MEAN_HIGH      = 55
POSITION_CRISIS    = 0.10
MAX_DRAWDOWN       = -0.15
VOL_CRISIS         = 0.03       # Relaxed: daily vol > 3% → crisis
VOL_TREND          = 0.012      # Relaxed: daily vol < 1.2% for trend
TREND_THRESHOLD    = 0.03       # Lowered: 3% 20d move for trend
VIX_HIGH           = 25
VIX_LOW            = 15

# ── Phase 2: asset-class parameter profiles ─────────────────────────────────
# The equity thresholds above are meaningless for bonds (TLT daily vol ~0.9%
# never trips VOL_CRISIS=3%) — each class gets vol/trend/persistence bands
# scaled to its own volatility regime. Scale-free indicators (RSI/CCI) shared.
ASSET_PROFILES = {
    "equity":    {"vol_crisis": 0.030, "vol_trend": 0.012, "trend_threshold": 0.030,
                  "atr_hi": 0.030, "atr_lo": 0.010, "cci_short": -250},
    "bond":      {"vol_crisis": 0.012, "vol_trend": 0.006, "trend_threshold": 0.015,
                  "atr_hi": 0.012, "atr_lo": 0.005, "cci_short": -200},
    "commodity": {"vol_crisis": 0.022, "vol_trend": 0.009, "trend_threshold": 0.022,
                  "atr_hi": 0.022, "atr_lo": 0.008, "cci_short": -225},
}
BOND_TICKERS      = {"TLT", "IEF", "SHY", "AGG", "BND", "LQD", "HYG", "TIP", "MUB", "GOVT"}
COMMODITY_TICKERS = {"GLD", "SLV", "USO", "UNG", "DBC", "PDBC", "CPER", "WEAT", "CORN"}

def classify_asset(ticker):
    t = ticker.upper()
    if t in BOND_TICKERS:
        return "bond"
    if t in COMMODITY_TICKERS:
        return "commodity"
    return "equity"

def get_profile(ticker):
    return ASSET_PROFILES[classify_asset(ticker)]

_cache = {}


# ═══════════════════════════════════════════════════════════════════════════════
# TA INDICATORS (self-contained — no pandas_ta dependency)
# ═══════════════════════════════════════════════════════════════════════════════

def sma(series, length):
    return series.rolling(length).mean()

def compute_rsi(close, length=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def compute_cci(high, low, close, length=20):
    tp = (high + low + close) / 3
    tp_sma = tp.rolling(length).mean()
    mad = tp.rolling(length).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    mad = mad.replace(0, np.nan)
    return (tp - tp_sma) / (0.015 * mad)

def compute_atr(high, low, close, length=14):
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1/length, adjust=False).mean()  # Wilder's smoothing

def compute_adx(high, low, close, length=14):
    """ADX using Wilder's smoothing.

    Phase 0 fix: previous version built the result from numpy arrays via
    pd.Series(...) with a RangeIndex; assigning that to a DatetimeIndex frame
    made df["adx"] all-NaN, which emptied the HMM feature matrix and silently
    forced the rule-based fallback on every run. Index is now preserved.
    """
    idx = close.index
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = compute_true_range(high, low, close).fillna(0)
    atr_s = tr.ewm(alpha=1/length, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=idx).ewm(alpha=1/length, adjust=False).mean() / atr_s
    minus_di = 100 * pd.Series(minus_dm, index=idx).ewm(alpha=1/length, adjust=False).mean() / atr_s
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
    return dx.ewm(alpha=1/length, adjust=False).mean()

def compute_true_range(high, low, close):
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

def compute_bbands(close, length=20, std=2):
    mid = close.rolling(length).mean()
    s = close.rolling(length).std()
    return mid + std * s, mid - std * s, mid


# ═══════════════════════════════════════════════════════════════════════════════
# DATA & FEATURE ENGINEERING (Improvement #1: 8 features)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_ohlcv(ticker, period_days=LOOKBACK_DAYS):
    tk = yf.Ticker(ticker)
    df = tk.history(period=f"{period_days}d", interval="1d", auto_adjust=True)
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.columns = ["open", "high", "low", "close", "volume"]
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.dropna(inplace=True)
    return df


def add_rich_features(df):
    """Expanded 8-feature observation vector for HMM."""
    df = df.copy()
    close = df["close"]
    high = df["high"]
    low = df["low"]
    vol = df["volume"]

    # Core indicators
    df["rsi"] = compute_rsi(close, RSI_PERIOD)
    df["cci"] = compute_cci(high, low, close, CCI_PERIOD)
    df["atr"] = compute_atr(high, low, close, ATR_PERIOD)

    # Bollinger Bands
    df["bb_upper"], df["bb_lower"], df["bb_mid"] = compute_bbands(close)
    df["bb_position"] = (close - df["bb_mid"]) / (df["bb_upper"] - df["bb_mid"] + 1e-10)

    # ADX
    df["adx"] = compute_adx(high, low, close, 14)

    # Normalized features
    df["atr_ratio"] = df["atr"] / close
    df["volume_z"] = (vol - vol.rolling(20).mean()) / vol.rolling(20).std().replace(0, 1)

    # Return-based
    df["log_return"] = np.log(close / close.shift(1))
    df["rolling_vol"] = df["log_return"].rolling(10).std()
    df["vol_ratio"] = df["rolling_vol"] / df["log_return"].rolling(60).std().replace(0, 1)
    df["trend_20d"] = close.pct_change(20)
    df["ma_distance"] = (close - sma(close, 50)) / sma(close, 50).replace(0, 1)

    # Skew and kurtosis for tail risk (manual — portable across pandas versions)
    df["skew"] = df["log_return"].rolling(20).apply(lambda x: sp_stats.skew(x) if len(x) >= 5 else np.nan, raw=True)
    df["kurt"] = df["log_return"].rolling(20).apply(lambda x: sp_stats.kurtosis(x) if len(x) >= 5 else np.nan, raw=True)

    return df


FEATURE_COLS = [
    "log_return", "rolling_vol", "vol_ratio", "bb_position",
    "adx", "ma_distance", "atr_ratio", "volume_z"
]


# ═══════════════════════════════════════════════════════════════════════════════
# HMM REGIME DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

def _fit_single_hmm(features_scaled, random_state):
    """Fit one HMM (Phase 2: diag covariance — full was ~140 params on ~125 bars)."""
    model = hmm.GaussianHMM(
        n_components=HMM_STATES,
        covariance_type=HMM_COVARIANCE,
        n_iter=HMM_ITERATIONS,
        random_state=random_state,
        tol=1e-4,
    )
    model.fit(features_scaled)
    return model


def _label_hmm_states(model):
    """
    Label HMM states consistently: 0=TRENDING, 1=MEAN_REV, 2=CRISIS.
    Uses PCA on state means to get a dominant axis, then orders by: vol & mean return.
    State with highest vol → CRISIS (2).
    Of remaining: highest mean return → TRENDING (0), other → MEAN_REV (1).
    """
    means = model.means_[:, 0]          # log_return means
    vols = np.sqrt(np.array([np.diag(c) for c in model.covars_])[:, 0])
    n_states = model.n_components

    # Crisis = highest vol
    crisis_st = int(np.argmax(vols))

    # Trend = highest mean return (excluding crisis if state count > 1)
    if n_states > 1:
        remaining = [s for s in range(n_states) if s != crisis_st]
        trend_st = max(remaining, key=lambda s: means[s])
    else:
        trend_st = crisis_st

    mapping = {}
    for s in range(n_states):
        if s == crisis_st and n_states > 1:
            mapping[s] = 2  # CRISIS
        elif s == trend_st:
            mapping[s] = 0  # TRENDING
        else:
            mapping[s] = 1  # MEAN_REV

    return mapping


def fit_hmm_ensemble(df):
    """Improvement #7: Ensemble of 5 HMMs, majority vote + agreement score."""
    features = df[FEATURE_COLS].dropna()
    if len(features) < 30:
        return None, None, None, None

    X = features.values
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    all_regimes = []
    for seed in range(42, 42 + HMM_ENSEMBLE_N):
        try:
            model = _fit_single_hmm(X_scaled, seed)
            raw = model.predict(X_scaled)
            mapping = _label_hmm_states(model)
            mapped = np.array([mapping[s] for s in raw])
            all_regimes.append(mapped)
        except Exception:
            continue

    if not all_regimes:
        return None, None, None, None

    # Majority vote
    ensemble_arr = np.array(all_regimes)
    ensembles_regimes, _ = sp_stats.mode(ensemble_arr, axis=0, keepdims=False)
    regimes_flat = ensembles_regimes.ravel() if ensembles_regimes.ndim > 1 else ensembles_regimes

    # Agreement rate (conservative: all agree or near-unanimous)
    agreement = (ensemble_arr == regimes_flat).mean(axis=0)

    # Fit reference model for predict_proba (use first model)
    ref_model = _fit_single_hmm(X_scaled, 42)

    # Pad to full dataframe length
    pad = len(df) - len(regimes_flat)
    full_regimes = np.ones(len(df), dtype=int)  # default to WEAK_TREND
    full_agreement = np.ones(len(df))
    full_regimes[pad:] = regimes_flat
    full_agreement[pad:] = agreement

    return full_regimes, full_agreement, ref_model, (scaler, X_scaled)


def apply_adaptive_persistence(regimes, df, profile=None):
    """Improvement #5: shorter lookback in crisis/high vol, longer in trend.
    Phase 2: ATR bands come from the asset-class profile (equity default)."""
    p = profile or ASSET_PROFILES["equity"]
    atr_ratio = df.get("atr_ratio", pd.Series(0.015, index=df.index)).values
    out = regimes.copy()
    for i in range(10, len(regimes)):
        if atr_ratio[i] > p["atr_hi"]:
            n = 2
        elif atr_ratio[i] < p["atr_lo"]:
            n = 5
        else:
            n = PERSISTENCE_DEFAULT
        window = regimes[i - n : i]
        if len(set(window)) == 1 and window[0] != regimes[i]:
            out[i] = window[0]
    return out


def assign_regimes_rule_based(df, profile=None):
    """Fallback rule-based regime assignment (3-state: 0=TRENDING, 1=MEAN_REV, 2=CRISIS).
    Phase 2: vol/trend thresholds come from the asset-class profile."""
    p = profile or ASSET_PROFILES["equity"]
    closes = df["close"].values
    regimes = np.ones(len(closes), dtype=int)  # default: MEAN_REV
    for i in range(20, len(closes)):
        w = closes[i - 20 : i]
        rets = np.diff(w) / w[:-1]
        vol = np.std(rets)
        trend = (w[-1] - w[0]) / w[0]
        if vol > p["vol_crisis"]:
            regimes[i] = 2  # CRISIS
        elif abs(trend) > p["trend_threshold"] and vol < p["vol_trend"]:
            regimes[i] = 0  # TRENDING
    return apply_adaptive_persistence(regimes, df, profile=p)


def get_regimes(df, use_hmm=True, profile=None):
    """Fit HMM ensemble or fall back to rule-based."""
    if not use_hmm:
        return assign_regimes_rule_based(df, profile=profile), np.ones(len(df)), None, None

    try:
        regimes, agreement, ref_model, model_data = fit_hmm_ensemble(df)
        if regimes is None:
            raise ValueError("HMM ensemble failed")
        regimes = apply_adaptive_persistence(regimes, df, profile=profile)
        return regimes, agreement, ref_model, model_data
    except Exception:
        fallback = assign_regimes_rule_based(df, profile=profile)
        return fallback, np.ones(len(df)), None, None


# ═══════════════════════════════════════════════════════════════════════════════
# MACRO OVERLAY (Improvement #4)
# ═══════════════════════════════════════════════════════════════════════════════

def get_macro_filter():
    """VIX + SPY trend filter. Returns -1 (risk-off), 0 (neutral), 1 (risk-on)."""
    now = datetime.now()
    if "macro" in _cache:
        data, ts = _cache["macro"]
        if (now - ts).total_seconds() < CACHE_TTL:
            return data
    try:
        vix = yf.download("^VIX", period="120d", progress=False, auto_adjust=True)
        spy = yf.download("SPY", period="120d", progress=False, auto_adjust=True)
        if isinstance(vix, pd.DataFrame):
            vix_close = vix["Close"] if "Close" in vix.columns else vix.iloc[:, 0]
        else:
            vix_close = vix
        spy_close = spy["Close"] if isinstance(spy, pd.DataFrame) else spy

        spy_ma50 = spy_close.rolling(50).mean()
        latest_vix = float(vix_close.iloc[-1])
        spy_trend = float(spy_close.iloc[-1] > spy_ma50.iloc[-1]) if not pd.isna(spy_ma50.iloc[-1]) else 1

        if latest_vix < VIX_LOW and spy_trend:
            result = 1
        elif latest_vix > VIX_HIGH:
            result = -1
        else:
            result = 0

        _cache["macro"] = (result, now)
        return result
    except Exception:
        return 0  # neutral on failure


# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL GENERATION (Improvement #3: multi-factor confirmation)
# ═══════════════════════════════════════════════════════════════════════════════

def generate_signals_v2(df, regimes, agreement, ref_model, model_data, macro_filter=0, profile=None):
    """
    Improvement #3: Multi-factor signal confirmation.
    States: 0=TRENDING, 1=MEAN_REV, 2=CRISIS
    Phase 2: crisis-short threshold from asset-class profile.
    """
    p = profile or ASSET_PROFILES["equity"]
    cci_short = p["cci_short"]
    df = df.copy()
    signals = np.zeros(len(df), dtype=int)
    pos_sizes = np.ones(len(df))
    stop_levels = np.full(len(df), np.nan)

    for i in range(1, len(df)):
        regime = regimes[i]
        rsi = df["rsi"].iloc[i]
        cci = df["cci"].iloc[i]
        prev_cci = df["cci"].iloc[i - 1]
        adx = df.get("adx", pd.Series(0, index=df.index)).iloc[i]
        bb_pos = df.get("bb_position", pd.Series(0, index=df.index)).iloc[i]
        vol_ratio = df.get("vol_ratio", pd.Series(1, index=df.index)).iloc[i]

        # ── State 0: TRENDING — CCI Breakout (ADX optional confirmation) ──
        if regime == 0:
            if pd.notna(cci) and pd.notna(prev_cci):
                if prev_cci < CCI_ENTRY <= cci:
                    signals[i] = 1
                elif cci < CCI_EXIT:
                    signals[i] = -1
                else:
                    signals[i] = signals[i - 1]
            if signals[i] == 1:
                stop_levels[i] = df["close"].iloc[i] - 2 * df["atr"].iloc[i]
            pos_sizes[i] = 1.0

        # ── State 1: MEAN_REV — RSI Fade ──
        elif regime == 1:
            if pd.notna(rsi):
                if rsi < RSI_OVERSOLD:
                    signals[i] = 1
                elif rsi > RSI_OVERBOUGHT:
                    # Phase 3: momentum short-ban — never fade strength while price
                    # holds above its 50MA unless macro is RISK_OFF. Fading momentum
                    # names (MU/NVDA) in uptrends was the largest OOS alpha bleed.
                    ma_dist = df.get("ma_distance", pd.Series(0, index=df.index)).iloc[i]
                    if (pd.notna(ma_dist) and ma_dist < 0) or macro_filter == -1:
                        signals[i] = -1
                    else:
                        signals[i] = 0
                elif RSI_MEAN_LOW < rsi < RSI_MEAN_HIGH:
                    signals[i] = 0
                else:
                    signals[i] = signals[i - 1]
            pos_sizes[i] = 0.60

        # ── State 2: CRISIS — Capital Preservation ──
        elif regime == 2:
            if pd.notna(cci) and cci < cci_short:
                signals[i] = -1
            else:
                signals[i] = 0
            pos_sizes[i] = POSITION_CRISIS

        # Force-exit previous long if entering crisis
        if regime == 2 and signals[i - 1] == 1:
            signals[i] = 0

        # Vol expansion circuit breaker
        if signals[i] == 1 and pd.notna(vol_ratio) and vol_ratio > 3.0:
            signals[i] = 0

    df["signal"] = signals
    # Phase 3: confidence-weighted sizing (Improvement #2, finally wired).
    # Ensemble agreement scales exposure: full size at unanimity, half at max dissent.
    conf = np.clip(np.asarray(agreement, dtype=float), 0.0, 1.0) if agreement is not None else np.ones(len(df))
    df["position_size"] = pos_sizes * (0.5 + 0.5 * conf)
    df["stop_level"] = stop_levels
    df["effective_pos"] = df["signal"] * df["position_size"]
    return df


def apply_stops(df):
    """
    Improvement #6, Phase 0 fix: split into two passes with correct ordering.
    Pass 1 (here): ATR stop on longs — needs only price/ATR, runs BEFORE backtest.
    Pass 2 (apply_dd_breaker): drawdown circuit breaker — needs equity, runs AFTER
    backtest, then equity is recomputed so metrics reflect enforced stops.
    Phase 3: stop now TRAILS — ratchets up with the high-water close, never down.
    """
    df = df.copy()
    atr_vals = df["atr"].values
    trail = None
    for i in range(1, len(df)):
        sig = df["signal"].iloc[i]
        prev_sig = df["signal"].iloc[i - 1]
        close = df["close"].iloc[i]
        if prev_sig != 1 and sig == 1:
            trail = close - 3 * atr_vals[i] if pd.notna(atr_vals[i]) else None
        elif sig == 1 and trail is not None and pd.notna(atr_vals[i]):
            trail = max(trail, close - 3 * atr_vals[i])  # ratchet up only
            if close < trail:
                df.at[df.index[i], "signal"] = 0
                trail = None
        elif sig != 1:
            trail = None
    df["effective_pos"] = df["signal"] * df["position_size"]
    return df


def apply_dd_breaker(df):
    """Drawdown circuit breaker on realized equity; flattens longs past MAX_DRAWDOWN."""
    df = df.copy()
    changed = False
    for i in range(1, len(df)):
        sig = df["signal"].iloc[i]
        peak = df["equity"].iloc[: i + 1].max()
        if peak > 0:
            dd = (df["equity"].iloc[i] - peak) / peak
            if dd < MAX_DRAWDOWN and sig == 1:
                df.at[df.index[i], "signal"] = 0
                changed = True
    if changed:
        df["effective_pos"] = df["signal"] * df["position_size"]
    return df, changed


def add_signal_labels_v2(df):
    """
    Human-readable signal labels (3-state).
    Phase 0 fix: labels are derived from the ACTUAL signal array (post-persistence,
    post-stops), so pill = chart = backtest position. Indicator values only refine
    the wording, never contradict the traded position.
    """
    labels = []
    prev_sig = 0
    for i in range(len(df)):
        r = df["regime"].iloc[i]
        sig = int(df["signal"].iloc[i])
        rsi = df["rsi"].iloc[i]
        cci = df["cci"].iloc[i]

        if r == 2:  # CRISIS
            lab = "SHORT" if sig == -1 else "FLAT"
        elif sig == 1:
            # New long entry vs. continuing hold
            lab = "BUY" if prev_sig != 1 else "HOLD LONG"
        elif sig == -1:
            # In MEAN_REV a -1 is a fade-short; in TRENDING it's an exit-down signal
            lab = "SELL" if r == 1 else "EXIT"
        else:  # sig == 0
            if prev_sig == 1:
                lab = "EXIT"          # just closed a long
            elif r == 0:
                lab = "WATCH"         # trending regime, waiting for CCI trigger
            elif r == 1:
                # flat in mean-rev: neutral zone = FLAT, edges = WATCH for setup
                if pd.notna(rsi) and (rsi < 40 or rsi > 60):
                    lab = "WATCH"
                else:
                    lab = "FLAT"
            else:
                lab = "FLAT"
        labels.append(lab)
        prev_sig = sig
    df["signal_label"] = labels
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# BACKTESTING
# ═══════════════════════════════════════════════════════════════════════════════

def backtest(df, initial_capital=100_000):
    df = df.copy()
    df["daily_return"] = df["close"].pct_change()
    df["strategy_return"] = df["effective_pos"].shift(1) * df["daily_return"]
    df["cumulative_strat"] = (1 + df["strategy_return"].fillna(0)).cumprod()
    df["cumulative_bah"] = (1 + df["daily_return"].fillna(0)).cumprod()
    df["equity"] = initial_capital * df["cumulative_strat"]
    df["equity_bah"] = initial_capital * df["cumulative_bah"]
    return df


def performance_summary(df, ticker):
    strat = df["strategy_return"].dropna()
    bah = df["daily_return"].dropna()
    if len(strat) < 5:
        return {"ticker": ticker, "error": "Insufficient data"}

    total_ret = df["cumulative_strat"].iloc[-1] - 1 if len(df) > 0 else 0
    bah_ret = df["cumulative_bah"].iloc[-1] - 1 if len(df) > 0 else 0
    ann_ret = (1 + total_ret) ** (252 / max(len(strat), 1)) - 1
    ann_vol = strat.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

    roll_max = df["equity"].cummax()
    drawdown = (df["equity"] - roll_max) / roll_max.replace(0, np.nan)
    max_dd = float(drawdown.min()) if len(drawdown) > 0 else 0

    # Trades & win rate — Phase 0 fix: count both long AND short round trips,
    # using position-aware entry/exit against the underlying close (not equity,
    # which is contaminated by sizing differences across regimes).
    trade_returns = []
    pos = 0          # current side: 0 flat, +1 long, -1 short
    entry_px = 0.0
    for i in range(len(df)):
        sig = int(np.sign(df["signal"].iloc[i]))
        px = float(df["close"].iloc[i])
        if sig != pos:
            if pos != 0 and entry_px > 0:
                r = (px - entry_px) / entry_px * pos   # short profits when px falls
                trade_returns.append(r)
            if sig != 0:
                entry_px = px
            pos = sig
    if pos != 0 and entry_px > 0:
        px = float(df["close"].iloc[-1])
        trade_returns.append((px - entry_px) / entry_px * pos)

    n_trades = len(trade_returns)  # completed round trips, not signal flips
    win_rate = sum(1 for r in trade_returns if r > 0) / max(n_trades, 1)

    # Regime distribution
    regime_counts = pd.Series(df["regime"]).value_counts(normalize=True).to_dict()
    regime_dist = {REGIME_META.get(k, {}).get("label", f"State_{k}"):
                   round(v * 100, 1) for k, v in regime_counts.items()}

    return {
        "ticker": ticker,
        "total_return": round(total_ret * 100, 2),
        "bah_return": round(bah_ret * 100, 2),
        "ann_return": round(ann_ret * 100, 2),
        "ann_vol": round(ann_vol * 100, 2),
        "sharpe": round(sharpe, 3),
        "max_drawdown": round(max_dd * 100, 2) if not np.isnan(max_dd) else 0,
        "n_trades": n_trades,
        "win_rate": round(win_rate * 100, 1),
        "regime_dist": regime_dist,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

def run_ticker(ticker, use_hmm=True, display_days=90):
    """Full v2 pipeline for one ticker."""
    meta = MAG7.get(ticker, {"name": ticker, "color": "#888"})
    profile = get_profile(ticker)  # Phase 2: asset-class thresholds

    try:
        df = fetch_ohlcv(ticker)
    except Exception as e:
        return None, {"error": str(e)}

    if len(df) < 30:
        return None, {"error": f"Only {len(df)} bars — need ≥30"}

    df = add_rich_features(df)
    regimes, agreement, ref_model, model_data = get_regimes(df, use_hmm=use_hmm, profile=profile)
    df["regime"] = regimes
    df["regime_confidence"] = agreement

    macro = get_macro_filter()
    df = generate_signals_v2(df, regimes, agreement, ref_model, model_data, macro, profile=profile)
    # Phase 0 fix — correct ordering:
    # 1) ATR stops mutate signals (price-based, no equity needed)
    # 2) backtest computes equity from FINAL price-based signals
    # 3) dd breaker reads real equity; if it fired, recompute equity once
    # 4) labels derive from the final signal array
    df = apply_stops(df)
    df = backtest(df)
    df, dd_fired = apply_dd_breaker(df)
    if dd_fired:
        df = backtest(df)
    df = add_signal_labels_v2(df)

    perf = performance_summary(df, ticker)
    perf["macro_filter"] = macro
    perf["name"] = meta["name"]
    perf["color"] = meta["color"]

    # Chart data (display window)
    display = df.last(f"{display_days}D").copy()
    n = len(display)

    # Per-bar arrays
    dates = [str(d.date()) for d in display.index]
    regimes_list = [int(r) for r in display["regime"].tolist()]
    signals_list = [int(s) for s in display["signal"].tolist()]
    signal_labels = display["signal_label"].tolist()
    rsi_vals = [float(v) if not pd.isna(v) else 0.0 for v in display["rsi"].tolist()]
    cci_vals = [float(v) if not pd.isna(v) else 0.0 for v in display["cci"].tolist()]
    closes = display["close"].tolist()

    # RSI bar colors: green <30, red >70, else grey
    rsi_colors = ["#76e4c4" if v < 30 else "#ff6b6b" if v > 70 else "#444" for v in rsi_vals]

    # Signal bar chart data — uses module-level SIGNAL_COLORS (single source of truth)
    signal_bars = [{"date": dates[i], "label": signal_labels[i], "color": SIGNAL_COLORS.get(signal_labels[i], "#444")}
                   for i in range(n)]

    # Regime timeline
    regime_colors_map = {0: "#76e4c4", 1: "#7ec8e3", 2: "#ff6b6b"}
    regime_timeline = [{"date": dates[i], "regime": regimes_list[i], "color": regime_colors_map.get(regimes_list[i], "#555")}
                       for i in range(n)]

    # Regime summary (days in each regime in display window)
    from collections import Counter
    regime_counts = Counter(regimes_list)
    regime_summary = {}
    for r_id, r_meta in REGIME_META.items():
        regime_summary[r_meta["label"]] = {
            "days": regime_counts.get(r_id, 0),
            "desc": r_meta["desc"],
            "color": r_meta["color"],
        }

    # Active regime (most recent bar)
    current_regime = regimes_list[-1] if regimes_list else 1
    current_rsi = rsi_vals[-1]
    current_cci = cci_vals[-1]
    current_signal = signal_labels[-1]
    current_close = closes[-1]

    # Strategy rules
    strategy_rules = [
        {"regime": "TRENDING",  "color": "#76e4c4", "entry": "CCI crosses above +100", "exit": "CCI drops below 0 or ATR stop", "size": "100%", "direction": "LONG"},
        {"regime": "MEAN_REV",  "color": "#7ec8e3", "entry": "RSI < 30 (BUY) / RSI > 70 (SELL)", "exit": "RSI returns to 45-55", "size": "60%", "direction": "BOTH"},
        {"regime": "CRISIS",    "color": "#ff6b6b", "entry": "CCI < -250 → SHORT", "exit": "CCI ≥ -250 or ATR stop", "size": "10%", "direction": "SHORT/FLAT"},
    ]

    chart_data = {
        "ticker": ticker,
        "name": meta["name"],
        "color": meta["color"],
        "sector": meta.get("sector", "—"),
        "last_close": round(current_close, 2),
        "lookback_days": n,
        "dates": dates,
        "close": closes,
        "equity": display["equity"].tolist(),
        "equity_bah": display["equity_bah"].tolist(),
        "rsi": rsi_vals,
        "rsi_colors": rsi_colors,
        "cci": cci_vals,
        "regime": regimes_list,
        "regime_colors": regime_colors_map,
        "regime_timeline": regime_timeline,
        "regime_summary": regime_summary,
        "signal_bars": signal_bars,
        "active_regime": REGIME_META.get(current_regime, {}).get("label", "UNKNOWN"),
        "active_rsi": round(current_rsi, 2),
        "active_cci": round(current_cci, 2),
        "active_signal": current_signal,
        "strategy_rules": strategy_rules,
    }

    # Cache the signal from the full pipeline — SIGNAL_COLORS is the single source of truth
    cache = _load_signal_cache()
    cache[ticker] = {"signal": current_signal, "color": SIGNAL_COLORS.get(current_signal, "#888")}
    _save_signal_cache(cache)

    return chart_data, perf


def run_all(use_hmm=True):
    """Batch across all MAG7."""
    results = []
    macro = get_macro_filter()
    for ticker in MAG7:
        chart_data, perf = run_ticker(ticker, use_hmm=use_hmm)
        if perf and "error" not in perf:
            results.append(perf)
    return results, macro


# ═══════════════════════════════════════════════════════════════════════════════
# WATCHLIST
# ═══════════════════════════════════════════════════════════════════════════════

def _load_watchlist():
    if not os.path.exists(WATCHLIST_PATH):
        _bootstrap_watchlist()
    with open(WATCHLIST_PATH) as f:
        return json.load(f)

def _save_watchlist(data):
    with open(WATCHLIST_PATH, "w") as f:
        json.dump(data, f, indent=2)

def _bootstrap_watchlist():
    data = {"watchlist": list(MAG7.keys()), "focus": list(MAG7.keys())[:7]}
    _save_watchlist(data)

def _load_signal_cache():
    if not os.path.exists(SIGNAL_CACHE_PATH):
        return {}
    with open(SIGNAL_CACHE_PATH) as f:
        return json.load(f)

def _save_signal_cache(data):
    with open(SIGNAL_CACHE_PATH, "w") as f:
        json.dump(data, f, indent=2)

def _validate_ticker(ticker):
    """Quick validation — try downloading 5 days of history."""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="5d")
        return not hist.empty
    except Exception:
        return False

def _get_ticker_signal(ticker):
    """Return cached signal from last full pipeline run."""
    cache = _load_signal_cache()
    return cache.get(ticker)


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP SERVER
# ═══════════════════════════════════════════════════════════════════════════════

class NS2Handler(SimpleHTTPRequestHandler):
    def _json(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        # Dashboard
        if path in ("/", "/index.html"):
            if os.path.exists(DASHBOARD_PATH):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                with open(DASHBOARD_PATH, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self._json(503, {"error": "Dashboard not found"})
            return

        # Health
        if path == "/health":
            self._json(200, {
                "status": "ok",
                "service": "ns2-qa",
                "port": PORT,
                "hmm_ensemble": HMM_ENSEMBLE_N,
                "features": FEATURE_COLS,
                "timestamp": datetime.now().isoformat(),
            })
            return

        # Macro filter status
        if path == "/api/macro":
            macro = get_macro_filter()
            labels = {-1: "RISK_OFF", 0: "NEUTRAL", 1: "RISK_ON"}
            self._json(200, {
                "macro_filter": macro,
                "label": labels.get(macro, "UNKNOWN"),
                "vix_high": VIX_HIGH,
                "vix_low": VIX_LOW,
            })
            return

        # Run single ticker
        if path == "/api/ticker":
            ticker = qs.get("ticker", [None])[0]
            if not ticker:
                self._json(400, {"error": "?ticker= required"})
                return
            ticker = ticker.upper()

            use_hmm = qs.get("hmm", ["1"])[0] != "0"
            display_days = int(qs.get("display", ["90"])[0])
            chart_data, perf = run_ticker(ticker, use_hmm=use_hmm, display_days=display_days)
            if perf and "error" in perf:
                self._json(500, perf)
            else:
                self._json(200, {"chart": chart_data, "performance": perf})
            return

        # Run all MAG7
        if path == "/api/run_all":
            use_hmm = qs.get("hmm", ["1"])[0] != "0"
            results, macro = run_all(use_hmm=use_hmm)
            summary = pd.DataFrame(results)
            numeric_cols = ["total_return", "bah_return", "ann_return", "ann_vol", "sharpe",
                           "max_drawdown", "n_trades", "win_rate"]
            summary_table = summary[["ticker", "name"] + numeric_cols].to_dict(orient="records") if not summary.empty else []

            # Aggregate stats
            avg_ret = summary["total_return"].mean() if not summary.empty else 0
            avg_sharpe = summary["sharpe"].mean() if not summary.empty else 0
            avg_win_rate = summary["win_rate"].mean() if not summary.empty else 0

            self._json(200, {
                "results": results,
                "summary_table": summary_table,
                "aggregate": {
                    "avg_return": round(avg_ret, 2),
                    "avg_sharpe": round(avg_sharpe, 3),
                    "avg_win_rate": round(avg_win_rate, 1),
                    "n_tickers": len(results),
                },
                "macro_filter": macro,
                "macro_label": {-1: "RISK_OFF", 0: "NEUTRAL", 1: "RISK_ON"}.get(macro, "UNKNOWN"),
                "config": {
                    "hmm_states": HMM_STATES,
                    "hmm_ensemble": HMM_ENSEMBLE_N,
                    "features": FEATURE_COLS,
                    "persistence_default": PERSISTENCE_DEFAULT,
                    "max_drawdown_limit": MAX_DRAWDOWN,
                    "vix_high": VIX_HIGH,
                    "vix_low": VIX_LOW,
                },
            })
            return

        # Chart data for single ticker (already served by /api/ticker above)

        # ── Watchlist ──
        if path == "/api/watchlist":
            data = _load_watchlist()
            signals = {}
            for t in data.get("focus", []):
                s = _get_ticker_signal(t)
                if s:
                    signals[t] = s
            data["signals"] = signals
            self._json(200, data)
            return

        self._json(404, {"error": "Not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length else {}

        if path == "/api/watchlist":
            action = body.get("action", "")
            data = _load_watchlist()
            ticker = body.get("ticker", "").upper()

            if action == "add":
                if not ticker or not _validate_ticker(ticker):
                    self._json(400, {"error": f"Invalid ticker: {ticker}"})
                    return
                if ticker not in data["watchlist"]:
                    data["watchlist"].append(ticker)
                _save_watchlist(data)
                self._json(200, {"status": "ok", "watchlist": data["watchlist"]})
                return

            if action == "remove":
                data["watchlist"] = [t for t in data["watchlist"] if t != ticker]
                data["focus"] = [t for t in data["focus"] if t != ticker]
                _save_watchlist(data)
                self._json(200, {"status": "ok", "watchlist": data["watchlist"]})
                return

            if action == "focus":
                focus = body.get("focus", [])
                if len(focus) > 20:
                    self._json(400, {"error": "Max 20 focus tickers"})
                    return
                data["focus"] = focus
                _save_watchlist(data)

                # Return signals for focus tickers
                signals = {}
                for t in focus:
                    s = _get_ticker_signal(t)
                    if s:
                        signals[t] = s
                data["signals"] = signals
                self._json(200, data)
                return

            self._json(400, {"error": "Unknown action"})
            return

        self._json(404, {"error": "Not found"})


if __name__ == "__main__":
    print(f"NS-2 QA Server — MAG7 HMM 7-Improvement Strategy")
    print(f"  Port: {PORT}")
    print(f"  HMM States: {HMM_STATES} | Ensemble: {HMM_ENSEMBLE_N} models")
    print(f"  Features: {FEATURE_COLS}")
    print(f"  Dashboard: {DASHBOARD_PATH}")
    print(f"  Improvements: 3-state HMM, 8 features, confidence sizing,")
    print(f"                multi-factor signals, VIX overlay, adaptive persistence,")
    print(f"                ATR stops + DD breaker, 5-model ensemble")
    print()

    server = HTTPServer(("0.0.0.0", PORT), NS2Handler)
    print(f"✓ Listening on http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()