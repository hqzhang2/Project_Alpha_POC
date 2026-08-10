"""
NS-5 Regime Fetcher — FRED fetch seam + derived series pipeline.

Loads 18 FRED series + VIX/SPY/TLT (Yahoo) and computes derived series
for consumption by regime_model.RegimeClassifier.

JUNIOR (cheap model): mechanics only — FRED/Yahoo fetch, TTL cache, fail-open.
FRONTIER: methodology, thresholds, detection logic in regime_model.py.

Design:
  - FRED v1 API via stdlib urllib (py3.9-safe), key from env only.
  - TTL cache per cadence: daily 1h, weekly 6h, monthly 24h, quarterly 48h.
  - Yahoo fetch for VIX/SPY/TLT via yfinance (reuse common/data/yahoo.py
    pattern).
  - Derived series: CPI_YOY, GDP_QOQ_ANN, UNRATE_3M_CHG, CPI_TREND_3M,
    2S10S, BAA_AAA, USD_MOM_PCT, STOCK_BOND_CORR (60d rolling on NYSE
    trading days only).
  - fetch_regime_data(days_back=750) -> DataFrame with all required columns.
  - Fail-open: empty DataFrame on missing key or network error, never crash.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from datetime import datetime, timedelta

import pandas as pd

# ── FRED config ─────────────────────────────────────────────────────────
FRED_API_KEY_ENV = "FRED_API_KEY"
FRED_OBS_URL = "https://api.stlouisfed.org/fred/series/observations"

# Per-cadence cache TTL (seconds) — matches macro.py pattern
TTL = {"Daily": 3600, "Weekly": 21600, "Monthly": 86400, "Quarterly": 172800}
DEFAULT_TTL = 3600

# ── 18 FRED series catalog ──────────────────────────────────────────────
FRED_SERIES = [
    # Growth & Labor
    {"id": "GDPC1",   "name": "Real GDP",              "cadence": "Quarterly"},
    {"id": "UNRATE",  "name": "Unemployment Rate",     "cadence": "Monthly"},
    {"id": "ICSA",    "name": "Initial Jobless Claims", "cadence": "Weekly"},
    {"id": "CIVPART", "name": "Labor Force Participation", "cadence": "Monthly"},
    {"id": "CES0500000003", "name": "Avg Hourly Earnings", "cadence": "Monthly"},
    # Inflation
    {"id": "CPIAUCSL",   "name": "CPI",               "cadence": "Monthly"},
    {"id": "PCEPILFE",   "name": "Core PCE",          "cadence": "Monthly"},
    {"id": "T5YIFR",     "name": "5y5y Breakeven",    "cadence": "Daily"},
    # Monetary
    {"id": "FEDFUNDS",   "name": "Fed Funds",          "cadence": "Daily"},
    {"id": "DGS2",       "name": "2Y Treasury",        "cadence": "Daily"},
    {"id": "DGS10",      "name": "10Y Treasury",       "cadence": "Daily"},
    {"id": "DFII5",      "name": "5Y TIPS Real Yield", "cadence": "Daily"},
    # Credit
    {"id": "BAA10Y",         "name": "BAA Corporate Yield", "cadence": "Daily"},
    {"id": "AAA10Y",         "name": "AAA Corporate Yield", "cadence": "Daily"},
    {"id": "NFCI",           "name": "Chicago Fed NFCI",    "cadence": "Weekly"},
    {"id": "BAMLH0A0HYM2",  "name": "HY OAS",               "cadence": "Daily"},
    # External
    {"id": "DTWEXBGS",      "name": "Trade-Weighted USD",  "cadence": "Daily"},
    {"id": "DCOILWTICO",    "name": "WTI Crude Oil",       "cadence": "Daily"},
]

SERIES_BY_ID = {s["id"]: s for s in FRED_SERIES}

# ── Thread-safe cache ───────────────────────────────────────────────────
_cache: dict = {}
_lock = threading.Lock()


def _fred_key() -> str:
    """FRED API key from env, or empty string."""
    return os.environ.get(FRED_API_KEY_ENV, "")


def _fetch_fred_series(series_id: str, start_date: str) -> list:
    """Fetch FRED observations as [{date, value}] from start_date.

    Fail-open: returns [] on missing key, network error, or bad response.
    """
    key = _fred_key()
    if not key:
        return []
    url = (
        f"{FRED_OBS_URL}?series_id={series_id}&api_key={key}"
        f"&file_type=json&observation_start={start_date}&sort_order=asc"
    )
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return []
    out = []
    for obs in data.get("observations", []):
        try:
            v = float(obs["value"])
        except (TypeError, ValueError):
            continue
        out.append({"date": obs["date"], "value": v})
    return out


def get_series(series_id: str, days_back: int = 800) -> list:
    """Cached FRED series observations. TTL per cadence.

    Returns [{date, value}] or [] on failure.
    """
    item = SERIES_BY_ID.get(series_id, {})
    cache_key = series_id
    with _lock:
        cached = _cache.get(cache_key)
        if cached and time.time() - cached[0] < TTL.get(
            item.get("cadence", ""), DEFAULT_TTL
        ):
            return cached[1]

    start = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    obs = _fetch_fred_series(series_id, start)
    with _lock:
        _cache[cache_key] = (time.time(), obs)
    return obs


# ── Yahoo fetch ─────────────────────────────────────────────────────────
def _fetch_yahoo_prices(tickers: list, period: str = "2y") -> dict:
    """Fetch daily Close prices for tickers via yfinance. Fail-open.

    Returns {ticker: pd.Series(index=date, dtype=float)} or empty on failure.
    """
    try:
        import yfinance as yf
    except ImportError:
        return {}
    try:
        px = yf.download(
            tickers, period=period, interval="1d",
            auto_adjust=True, progress=False,
        )
        if px.empty:
            return {}
        close = px["Close"] if isinstance(px.columns, pd.MultiIndex) else px
        result = {}
        for t in tickers:
            if t in close.columns:
                s = close[t].dropna()
                if not s.empty:
                    result[t] = s
            elif isinstance(close, pd.Series):
                result[t] = close.dropna()
                break  # single-ticker download
        return result
    except Exception:
        return {}


# ── Derived series computation ──────────────────────────────────────────
def _series_to_daily_df(series_list: list, col_name: str) -> pd.DataFrame:
    """Convert [{date, value}] -> single-column DataFrame indexed by date."""
    if not series_list:
        return pd.DataFrame()
    df = pd.DataFrame(series_list)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")[["value"]].rename(columns={"value": col_name})
    return df.sort_index()


def _resample_to_daily(series_df: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Resample a lower-frequency series to daily via forward-fill.

    Args:
        series_df: DataFrame with single column, datetime index.
        freq: 'ME' for monthly, 'QE' for quarterly, 'W' for weekly.
    """
    if series_df.empty:
        return series_df
    # Ensure we're at the proper frequency first
    resampled = series_df.resample(freq).last()
    # Forward-fill to daily (including weekends — classifier works on calendar days)
    daily = resampled.reindex(pd.date_range(resampled.index[0], resampled.index[-1], freq="D"))
    daily = daily.ffill()
    return daily


def _compute_derived_series(fred_data: dict, yahoo_data: dict) -> pd.DataFrame:
    """Compute all derived series from raw FRED + Yahoo data.

    Returns a daily DataFrame with all columns needed by RegimeClassifier.
    Calendar-day index (NOT NYSE trading days) — correlation computation
    aligns to NYSE separately.
    """
    frames = []

    # ── CPI_YOY: monthly CPI -> 12-month YoY -> daily ffill ─────────
    cpi = _series_to_daily_df(fred_data.get("CPIAUCSL", []), "CPI_LEVEL")
    if not cpi.empty:
        cpi_monthly = cpi.resample("ME").last()
        cpi_yoy = cpi_monthly.pct_change(12) * 100  # YoY in %
        cpi_yoy.columns = ["CPI_YOY"]
        cpi_yoy = _resample_to_daily(cpi_yoy, "ME")
        if not cpi_yoy.empty:
            frames.append(cpi_yoy)

    # ── GDP_QOQ_ANN: quarterly GDP -> QoQ annualized -> daily ffill ─
    gdp = _series_to_daily_df(fred_data.get("GDPC1", []), "GDP_LEVEL")
    if not gdp.empty:
        gdp_q = gdp.resample("QE").last()
        gdp_qoq = ((gdp_q / gdp_q.shift(1)) ** 4 - 1) * 100
        gdp_qoq.columns = ["GDP_QOQ_ANN"]
        gdp_qoq = _resample_to_daily(gdp_qoq, "QE")
        if not gdp_qoq.empty:
            frames.append(gdp_qoq)

    # ── UNRATE_3M_CHG: monthly UNRATE -> 3-month Δ -> daily ffill ──
    unrate = _series_to_daily_df(fred_data.get("UNRATE", []), "UNRATE")
    if not unrate.empty:
        unrate_m = unrate.resample("ME").last()
        unrate_3m = unrate_m.diff(3)  # 3-month change in pp
        unrate_3m.columns = ["UNRATE_3M_CHG"]
        unrate_3m = _resample_to_daily(unrate_3m, "ME")
        if not unrate_3m.empty:
            frames.append(unrate_3m)

    # ── CPI_TREND_3M: 3-month Δ of CPI_YOY ─────────────────────────
    if not cpi.empty:
        cpi_yoy_m = cpi.resample("ME").last().pct_change(12) * 100
        cpi_trend = cpi_yoy_m.diff(3)  # 3-month change in YoY
        cpi_trend.columns = ["CPI_TREND_3M"]
        cpi_trend = _resample_to_daily(cpi_trend, "ME")
        if not cpi_trend.empty:
            frames.append(cpi_trend)

    # ── 2S10S: DGS10 - DGS2 (daily, keep as-is) ────────────────────
    dgs2 = _series_to_daily_df(fred_data.get("DGS2", []), "DGS2")
    dgs10 = _series_to_daily_df(fred_data.get("DGS10", []), "DGS10")
    if not dgs2.empty and not dgs10.empty:
        spread = dgs10["DGS10"] - dgs2["DGS2"]
        frames.append(spread.rename("2S10S"))

    # ── BAA_AAA: BAA10Y - AAA10Y (daily, as-is) ────────────────────
    baa = _series_to_daily_df(fred_data.get("BAA10Y", []), "BAA10Y")
    aaa = _series_to_daily_df(fred_data.get("AAA10Y", []), "AAA10Y")
    if not baa.empty and not aaa.empty:
        spread = baa["BAA10Y"] - aaa["AAA10Y"]
        frames.append(spread.rename("BAA_AAA"))

    # ── Raw daily series (as-is) ────────────────────────────────────
    daily_raw = {
        "NFCI": "NFCI", "FEDFUNDS": "FEDFUNDS",
        "DCOILWTICO": "DCOILWTICO",
    }
    for sid, col in daily_raw.items():
        if sid in fred_data and fred_data[sid]:
            df = _series_to_daily_df(fred_data[sid], col)
            if not df.empty:
                frames.append(df)

    # ── USD_MOM_PCT: from DTWEXBGS daily ───────────────────────────
    usd = _series_to_daily_df(fred_data.get("DTWEXBGS", []), "USD_LEVEL")
    if not usd.empty:
        usd_mom = usd.pct_change(21) * 100  # ~1 month
        usd_mom.columns = ["USD_MOM_PCT"]
        frames.append(usd_mom)

    # ── STOCK_BOND_CORR: 60d rolling on NYSE trading days ONLY ─────
    sp = yahoo_data.get("SPY")
    tl = yahoo_data.get("TLT")
    if sp is not None and tl is not None and not sp.empty and not tl.empty:
        # Align to common dates (NYSE trading days)
        common = sp.index.intersection(tl.index)
        if len(common) >= 65:
            sp_ret = sp.loc[common].pct_change().dropna()
            tl_ret = tl.loc[common].pct_change().dropna()
            common_ret = sp_ret.index.intersection(tl_ret.index)
            if len(common_ret) >= 61:
                corr_series = sp_ret.loc[common_ret].rolling(60).corr(tl_ret.loc[common_ret])
                # rolling().corr() returns MultiIndex; extract the values
                if isinstance(corr_series.index, pd.MultiIndex):
                    corr_vals = corr_series.xs("SPY", level=1, drop_level=True)
                else:
                    corr_vals = corr_series
                corr_df = pd.DataFrame(
                    corr_vals.values, index=corr_vals.index, columns=["STOCK_BOND_CORR"]
                )
                # Reindex to calendar days and ffill
                full_idx = pd.date_range(corr_df.index[0], corr_df.index[-1], freq="D")
                corr_daily = corr_df.reindex(full_idx).ffill()
                if not corr_daily.empty:
                    frames.append(corr_daily)

    # ── VIX: from Yahoo ────────────────────────────────────────────
    vix = yahoo_data.get("^VIX")
    if vix is not None and not vix.empty:
        vix_df = vix.rename("VIX").to_frame()
        frames.append(vix_df)

    # ── Merge all into one daily DataFrame ─────────────────────────
    if not frames:
        return pd.DataFrame()
    result = frames[0]
    for f in frames[1:]:
        result = result.join(f, how="outer")
    # Forward-fill all columns (carry last known value)
    result = result.ffill()
    return result


def fetch_regime_data(days_back: int = 750) -> pd.DataFrame:
    """Fetch all FRED + Yahoo data, compute derived series, return daily panel.

    Returns a DataFrame with columns needed by RegimeClassifier.classify_dataframe().
    Fail-open: returns empty DataFrame on missing key or network error.
    """
    key = _fred_key()
    if not key:
        return pd.DataFrame()

    # Fetch all 18 FRED series concurrently? No — urllib is sync.
    # We fetch lazily: only the ones we need for computation.
    # Buffer: derived series need history BEFORE the window (CPI YoY =
    # 12-month lag, GDP QoQ = 1 quarter). Fetch with +400d buffer, trim
    # to the requested window below.
    fetch_days = days_back + 400
    fred_data = {}
    for s in FRED_SERIES:
        sid = s["id"]
        fred_data[sid] = get_series(sid, days_back=fetch_days)

    # Fetch Yahoo data
    yahoo_data = _fetch_yahoo_prices(["SPY", "TLT", "^VIX"], period="2y")

    # Compute derived series
    df = _compute_derived_series(fred_data, yahoo_data)

    # Fail-open: without the core FRED series (CPI/GDP/UNRATE) there is
    # nothing to classify — a Yahoo-only VIX frame is not a regime panel.
    # Missing key / bogus key / FRED down → empty DataFrame.
    required = {"CPI_YOY", "GDP_QOQ_ANN", "UNRATE_3M_CHG"}
    if df.empty or not required.issubset(df.columns):
        return pd.DataFrame()

    # Trim to requested window
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=days_back)
    df = df[df.index >= cutoff]

    return df


def clear_cache():
    """Clear all internal caches (useful for testing)."""
    global _cache
    with _lock:
        _cache.clear()
