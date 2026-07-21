#!/usr/bin/env python3
"""
NS-3: 3-Tier Sector Rotation Algo (PROD)
==========================================
Port 9236 - FastAPI backend using common library.
"""
import warnings
warnings.filterwarnings("ignore", category=RuntimeWarning)

import datetime
import os
import sys
import logging
from pathlib import Path

# Add common to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

# ── Logging ──────────────────────────────────────────────────────────────────

logger = logging.getLogger("ns3")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

import numpy as np
import pandas as pd
import yfinance as yf
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# Import from common library
from common import (
    get_ns_config,
    get_yahoo_client,
    get_etf_holdings,
    sma, ema, rsi, macd, bollinger_bands, bb_position,
    adx, atr, obv, obv_slope,
    fit_hmm,
    compute_all,
)

# ── Configuration ────────────────────────────────────────────────────────────

ns_cfg = get_ns_config()
PORT = int(os.environ.get("PORT", ns_cfg.ns3_port))
LOOKBACK_WKS = ns_cfg.ns3_lookback_weeks
HMM_STATES = ns_cfg.ns3_hmm_states
HMM_ITER = ns_cfg.ns3_hmm_iter
HMM_BULL_PROB_THRESHOLD = ns_cfg.ns3_hmm_bull_threshold
RS_PERCENTILE = ns_cfg.ns3_rs_percentile
PIOTROSKI_MIN = ns_cfg.ns3_piotroski_min
TA_SCORE_MIN = ns_cfg.ns3_ta_score_min

SECTORS = [
    {"symbol": "XLK", "name": "Technology"},
    {"symbol": "XLF", "name": "Financials"},
    {"symbol": "XLV", "name": "Healthcare"},
    {"symbol": "XLE", "name": "Energy"},
    {"symbol": "XLI", "name": "Industrials"},
    {"symbol": "XLY", "name": "Cons. Discret."},
    {"symbol": "XLP", "name": "Cons. Staples"},
    {"symbol": "XLB", "name": "Materials"},
    {"symbol": "XLRE", "name": "Real Estate"},
    {"symbol": "XLU", "name": "Utilities"},
    {"symbol": "XLC", "name": "Comm. Services"},
]

SPY_SYMBOL = "SPY"

ETF_HOLDINGS = {
    "XLE": ["XOM", "CVX", "COP", "EOG", "SLB", "MPC", "PSX", "VLO", "OXY", "HAL"],
    "XLP": ["PG", "KO", "PEP", "COST", "WMT", "PM", "MO", "MDLZ", "CL", "STZ"],
    "XLI": ["GE", "RTX", "CAT", "UNP", "HON", "UPS", "LMT", "DE", "ETN", "BA"],
    "XLK": ["MSFT", "AAPL", "NVDA", "AVGO", "CRM", "AMD", "ORCL", "QCOM", "TXN", "AMAT"],
    "XLF": ["JPM", "BRK-B", "BAC", "WFC", "GS", "MS", "BLK", "AXP", "SPGI", "CB"],
    "XLV": ["LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "DHR", "BMY", "ISRG"],
    "XLC": ["META", "GOOGL", "NFLX", "DIS", "CMCSA", "T", "VZ", "EA", "TTWO", "CHTR"],
    "XLY": ["AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "TJX", "BKNG", "ORLY"],
    "XLB": ["LIN", "APD", "ECL", "SHW", "FCX", "NEM", "NUE", "VMC", "MLM", "DD"],
    "XLRE": ["PLD", "AMT", "EQIX", "WELL", "DLR", "PSA", "O", "SPG", "AVB", "EQR"],
    "XLU": ["NEE", "SO", "DUK", "AEP", "SRE", "D", "EXC", "XEL", "WEC", "ES"],
}

app = FastAPI(title="NS-3 API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

yahoo = get_yahoo_client()


# ── Helpers: Data Fetching ───────────────────────────────────────────────────

import time as _time
from functools import lru_cache as _lru_cache

# 5-minute TTL cache for tier computations to prevent redundant yfinance calls
_tier_cache = {}
_TIER_CACHE_TTL = 300  # seconds


def _cache_get(key):
    if key in _tier_cache:
        val, ts = _tier_cache[key]
        if _time.time() - ts < _TIER_CACHE_TTL:
            return val
        del _tier_cache[key]
    return None


def _cache_set(key, val):
    _tier_cache[key] = (val, _time.time())


def fetch_weekly_closes(symbols: list, weeks: int = 14) -> pd.DataFrame:
    end = datetime.date.today()
    start = end - datetime.timedelta(weeks=weeks + 2)
    raw = yf.download(
        tickers=symbols, start=str(start), end=str(end),
        interval="1wk", auto_adjust=True, progress=False
    )
    if isinstance(raw.columns, pd.MultiIndex):
        closes = raw["Close"]
    else:
        closes = raw[["Close"]].rename(columns={"Close": symbols[0]})
    closes = closes.dropna().tail(weeks)
    return closes


def fetch_weekly_ohlcv(symbols: list, weeks: int = LOOKBACK_WKS) -> dict:
    end = datetime.date.today()
    start = end - datetime.timedelta(weeks=weeks + 4)
    raw = yf.download(
        symbols, start=str(start), end=str(end),
        interval="1wk", auto_adjust=True, progress=False
    )
    out = {}
    for sym in symbols:
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                df = pd.DataFrame({
                    "open": raw["Open"][sym],
                    "high": raw["High"][sym],
                    "low": raw["Low"][sym],
                    "close": raw["Close"][sym],
                    "volume": raw["Volume"][sym],
                }).dropna().tail(weeks)
            else:
                df = pd.DataFrame({
                    "open": raw["Open"],
                    "high": raw["High"],
                    "low": raw["Low"],
                    "close": raw["Close"],
                    "volume": raw["Volume"],
                }).dropna().tail(weeks)
            out[sym] = df
        except Exception as e:
            logger.warning(f"  OHLCV fetch error for {sym}: {e}")
    return out


# ── Tier 1: Sector Rotation ──────────────────────────────────────────────────

def compute_ratio_momentum(sector_prices: pd.Series, spy_prices: pd.Series) -> float:
    ratios = sector_prices / spy_prices
    ratios = ratios.dropna()
    if len(ratios) < 2:
        return 0.0
    oldest = ratios.iloc[0]
    newest = ratios.iloc[-1]
    return round(((newest - oldest) / oldest) * 100, 4)


def ytd_return(prices: pd.Series) -> float:
    """Year-to-date return. Works with timezone-aware and naive indices."""
    current_year = datetime.date.today().year
    try:
        this_year = prices[prices.index.year == current_year]
    except AttributeError:
        # Index may have no .year attribute (e.g., non-datetime index)
        return 0.0
    if len(this_year) < 2:
        return 0.0
    return round(((this_year.iloc[-1] - this_year.iloc[0]) / this_year.iloc[0]) * 100, 2)


def run_tier1() -> dict:
    cached = _cache_get("tier1")
    if cached is not None:
        return cached

    all_symbols = [s["symbol"] for s in SECTORS] + [SPY_SYMBOL]
    closes = fetch_weekly_closes(all_symbols, weeks=14)
    spy_prices = closes[SPY_SYMBOL]

    results = []
    for sector in SECTORS:
        sym = sector["symbol"]
        if sym not in closes.columns:
            continue
        sec_prices = closes[sym]
        aligned_spy = spy_prices.reindex(sec_prices.index).ffill()
        momentum = compute_ratio_momentum(sec_prices, aligned_spy)
        ytd = ytd_return(sec_prices)

        results.append({
            "symbol": sym,
            "name": sector["name"],
            "momentum": momentum,
            "ytd": ytd,
            "currentPrice": round(sec_prices.iloc[-1], 2),
        })

    results.sort(key=lambda x: x["momentum"], reverse=True)
    for i, r in enumerate(results):
        r["rank"] = i + 1
        r["passToTier2"] = i < 3

    result = {
        "generatedAt": datetime.datetime.utcnow().isoformat() + "Z",
        "sectors": results,
    }
    _cache_set("tier1", result)
    return result


# ── Tier 2: ETF Signal Engine ────────────────────────────────────────────────

def score_etf(df: pd.DataFrame) -> dict:
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    macd_line, macd_sig, macd_hist = macd(close)
    rsi_val = rsi(close)
    adx_val = adx(high, low, close)
    obv_val = obv(close, volume)
    slope = obv_slope(obv_val)

    bull_state, bull_prob, _ = fit_hmm(close)

    cur_macd = float(macd_line.iloc[-1])
    cur_sig = float(macd_sig.iloc[-1])
    cur_rsi = float(rsi_val.iloc[-1])
    cur_adx = float(adx_val.iloc[-1])
    cur_price = float(close.iloc[-1])

    hmm_bull = bull_prob >= HMM_BULL_PROB_THRESHOLD
    macd_bull = cur_macd > cur_sig
    adx_strong = cur_adx > 25
    rsi_ok = 45 <= cur_rsi <= 75
    obv_rising = slope > 0

    score = sum([hmm_bull, macd_bull, adx_strong, rsi_ok, obv_rising])

    if not hmm_bull:
        decision = "HOLD / AVOID"
    elif score >= 3:
        decision = "ENTER LONG"
    elif score == 2:
        decision = "WATCH"
    else:
        decision = "AVOID"

    return {
        "currentPrice": round(cur_price, 2),
        "score": score,
        "maxScore": 5,
        "decision": decision,
        "hmm": {
            "bullProb": round(bull_prob, 4),
            "isGated": hmm_bull,
            "threshold": HMM_BULL_PROB_THRESHOLD,
        },
        "macd": {"value": round(cur_macd, 4), "isBull": macd_bull},
        "adx": {"value": round(cur_adx, 2), "isStrong": adx_strong},
        "rsi": {"value": round(cur_rsi, 2), "isOk": rsi_ok},
        "obv": {"slope": round(slope, 2), "isRising": obv_rising},
    }


def run_tier2(tier1_data: dict) -> dict:
    cached = _cache_get("tier2")
    if cached is not None:
        return cached

    top3 = [s["symbol"] for s in tier1_data["sectors"] if s.get("passToTier2")]
    if not top3:
        top3 = ["XLE", "XLP", "XLI"]

    ohlcv = fetch_weekly_ohlcv(top3, weeks=LOOKBACK_WKS)

    results = []
    for sym in top3:
        if sym not in ohlcv:
            continue
        signals = score_etf(ohlcv[sym])
        results.append({"symbol": sym, **signals})

    result = {
        "generatedAt": datetime.datetime.utcnow().isoformat() + "Z",
        "tier1Source": "tier1_data.json",
        "etfs": results,
    }
    _cache_set("tier2", result)
    return result


# ── Tier 3: Stock Selection ──────────────────────────────────────────────────

def piotroski_fscore(ticker_obj) -> tuple:
    try:
        info = ticker_obj.info
        bs = ticker_obj.balance_sheet
        inc = ticker_obj.income_stmt
        cf = ticker_obj.cashflow

        def get(df, *keys):
            for k in keys:
                if df is not None and k in df.index:
                    vals = df.loc[k].dropna()
                    return vals.iloc[0] if len(vals) > 0 else None
            return None

        def get2(df, *keys):
            for k in keys:
                if df is not None and k in df.index:
                    vals = df.loc[k].dropna()
                    cur = vals.iloc[0] if len(vals) > 0 else None
                    pri = vals.iloc[1] if len(vals) > 1 else None
                    return cur, pri
            return None, None

        net_income, ni_prior = get2(inc, "Net Income")
        total_assets, ta_prior = get2(bs, "Total Assets")
        op_cf = get(cf, "Operating Cash Flow", "Cash Flow From Operations")

        roa_cur = (net_income / total_assets) if net_income and total_assets else None
        roa_pri = (ni_prior / ta_prior) if ni_prior and ta_prior else None

        f1 = int(roa_cur > 0) if roa_cur is not None else 0
        f2 = int(op_cf > 0) if op_cf is not None else 0
        f3 = int(roa_cur > roa_pri) if roa_cur is not None and roa_pri is not None else 0
        f4 = int(op_cf > net_income) if op_cf and net_income else 0

        ltd_cur, ltd_pri = get2(bs, "Long Term Debt", "Long-Term Debt")
        ca_cur, ca_pri = get2(bs, "Current Assets")
        cl_cur, cl_pri = get2(bs, "Current Liabilities")

        lev_cur = (ltd_cur / total_assets) if ltd_cur and total_assets else None
        lev_pri = (ltd_pri / ta_prior) if ltd_pri and ta_prior else None
        liq_cur = (ca_cur / cl_cur) if ca_cur and cl_cur else None
        liq_pri = (ca_pri / cl_pri) if ca_pri and cl_pri else None

        f5 = int(lev_cur < lev_pri) if lev_cur is not None and lev_pri is not None else 0
        f6 = int(liq_cur > liq_pri) if liq_cur is not None and liq_pri is not None else 0

        so_cur, so_pri = get2(bs, "Ordinary Shares Number", "Share Issued")
        f7 = int(so_cur <= so_pri) if so_cur is not None and so_pri is not None else 0

        rev_cur, rev_pri = get2(inc, "Total Revenue")
        cogs_cur, cogs_pri = get2(inc, "Cost Of Revenue", "Cost of Goods Sold")

        gm_cur = ((rev_cur - cogs_cur) / rev_cur) if rev_cur and cogs_cur else None
        gm_pri = ((rev_pri - cogs_pri) / rev_pri) if rev_pri and cogs_pri else None
        at_cur = (rev_cur / total_assets) if rev_cur and total_assets else None
        at_pri = (rev_pri / ta_prior) if rev_pri and ta_prior else None

        f8 = int(gm_cur > gm_pri) if gm_cur is not None and gm_pri is not None else 0
        f9 = int(at_cur > at_pri) if at_cur is not None and at_pri is not None else 0

        score = f1 + f2 + f3 + f4 + f5 + f6 + f7 + f8 + f9
        return score, {
            "ROA_positive": bool(f1), "CFO_positive": bool(f2),
            "ROA_improving": bool(f3), "accruals_low": bool(f4),
            "leverage_down": bool(f5), "liquidity_up": bool(f6),
            "no_dilution": bool(f7), "gross_margin_up": bool(f8),
            "asset_turnover_up": bool(f9),
        }
    except Exception as e:
        return 0, {"error": str(e)}


def relative_strength_26w(stock_close: pd.Series, etf_close: pd.Series) -> float:
    aligned = stock_close.reindex(etf_close.index).ffill()
    ratio = aligned / etf_close
    ratio = ratio.dropna()
    if len(ratio) < 2:
        return 0.0
    return ((ratio.iloc[-1] - ratio.iloc[0]) / ratio.iloc[0]) * 100


def ta_score_stock(df: pd.DataFrame) -> tuple:
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    macd_line, macd_sig, _ = macd(c)
    rsi_val = rsi(c)
    adx_val = adx(h, l, c)
    obv_val = obv(c, v)

    hmm_ok, hmm_prob, _ = fit_hmm(c)
    macd_ok = bool(macd_line.iloc[-1] > macd_sig.iloc[-1])
    adx_ok = bool(adx_val.iloc[-1] > 25)
    rsi_ok = bool(45 <= rsi_val.iloc[-1] <= 75)
    obv_ok = obv_slope(obv_val) > 0

    score = sum([hmm_ok, macd_ok, adx_ok, rsi_ok, obv_ok])
    return score, {
        "hmmOk": hmm_ok,
        "hmmProb": round(hmm_prob, 4),
        "macdOk": macd_ok,
        "adxOk": adx_ok,
        "rsiOk": rsi_ok,
        "obvOk": obv_ok,
    }


def run_tier3(tier2_data: dict) -> dict:
    qualifying_etfs = [
        e["symbol"] for e in tier2_data["etfs"]
        if e["decision"] in ("ENTER LONG", "WATCH")
    ]
    if not qualifying_etfs:
        qualifying_etfs = [e["symbol"] for e in tier2_data["etfs"]]

    end = datetime.date.today()
    start = end - datetime.timedelta(weeks=LOOKBACK_WKS + 4)

    all_sectors = []

    for etf_sym in qualifying_etfs:
        holdings = yahoo.get_etf_holdings(etf_sym)
        if not holdings:
            holdings = ETF_HOLDINGS.get(etf_sym, [])

        if not holdings:
            continue

        all_tickers = [etf_sym] + list(holdings.keys())
        raw = yf.download(
            all_tickers, start=str(start), end=str(end),
            interval="1wk", auto_adjust=True, progress=False
        )

        if isinstance(raw.columns, pd.MultiIndex):
            etf_close = raw["Close"][etf_sym].dropna()
        else:
            etf_close = raw["Close"].dropna()

        rs_scores = {}
        for sym in holdings.keys():
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    sc = raw["Close"][sym].dropna()
                else:
                    sc = raw["Close"].dropna()
                rs_scores[sym] = relative_strength_26w(sc, etf_close)
            except Exception as e:
                logger.warning(f"  RS calc error for {sym} vs {etf_sym}: {e}")
                rs_scores[sym] = -999.0

        rs_series = pd.Series(rs_scores).sort_values(ascending=False)
        cutoff_idx = max(1, int(len(rs_series) * (1 - RS_PERCENTILE)))
        top_rs = rs_series.index[:cutoff_idx].tolist()

        f_scores = {}
        for sym in top_rs:
            try:
                tk = yf.Ticker(sym)
                fs, breakdown = piotroski_fscore(tk)
                f_scores[sym] = (fs, breakdown)
            except Exception as e:
                logger.warning(f"  F-Score error for {sym}: {e}")
                f_scores[sym] = (0, {})

        passed_f = [s for s in top_rs if f_scores[s][0] >= PIOTROSKI_MIN]
        if len(passed_f) < 2:
            passed_f = sorted(top_rs, key=lambda s: f_scores[s][0], reverse=True)[:3]

        stock_results = []
        for sym in passed_f:
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    df = pd.DataFrame({
                        "close": raw["Close"][sym],
                        "high": raw["High"][sym],
                        "low": raw["Low"][sym],
                        "volume": raw["Volume"][sym],
                    }).dropna()
                else:
                    df = pd.DataFrame({
                        "close": raw["Close"],
                        "high": raw["High"],
                        "low": raw["Low"],
                        "volume": raw["Volume"],
                    }).dropna()

                score, ta = ta_score_stock(df)
                fs, breakdown = f_scores.get(sym, (0, {}))
                rs = rs_scores.get(sym, 0.0)

                hmm_gated = ta["hmmOk"]
                if not hmm_gated:
                    decision = "AVOID"
                elif score >= TA_SCORE_MIN:
                    decision = "BUY"
                elif score == 2:
                    decision = "WATCH"
                else:
                    decision = "AVOID"

                confidence = round(ta["hmmProb"] * (score / 5), 4)

                stock_results.append({
                    "symbol": sym,
                    "sectorEtf": etf_sym,
                    "decision": decision,
                    "confidence": confidence,
                    "rs26w": round(rs, 2),
                    "fscore": fs,
                    "taScore": score,
                    "fscoreBreakdown": breakdown,
                })
            except Exception as e:
                logger.warning(f"  TA score error for {sym}: {e}")

        stock_results.sort(key=lambda x: x["confidence"], reverse=True)
        stock_results = stock_results[:5]

        all_sectors.append({
            "etf": etf_sym,
            "stocks": stock_results,
        })

    return {
        "generatedAt": datetime.datetime.utcnow().isoformat() + "Z",
        "sectors": all_sectors,
    }


# ── API Endpoints ────────────────────────────────────────────────────────────

@app.get("/api/v1/health")
def health_check():
    return {"status": "ok", "service": "NS-3", "port": PORT}


@app.get("/api/v1/tier1")
def get_tier1():
    try:
        return run_tier1()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/tier2")
def get_tier2():
    try:
        tier1 = run_tier1()
        return run_tier2(tier1)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/tier3")
def get_tier3():
    try:
        tier1 = run_tier1()
        tier2 = run_tier2(tier1)
        return run_tier3(tier2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/all")
def get_all_tiers():
    try:
        tier1 = run_tier1()
        tier2 = run_tier2(tier1)
        tier3 = run_tier3(tier2)
        return {"tier1": tier1, "tier2": tier2, "tier3": tier3}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)