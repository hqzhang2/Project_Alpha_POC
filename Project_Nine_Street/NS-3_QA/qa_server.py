#!/usr/bin/env python3
"""
NS-3 QA Server - 3-Tier Sector Rotation (validated algorithm, deterministic)
=============================================================================
Tier 1: rank 11 sector ETFs by 52-week ratio momentum vs SPY (weekly bars).
        [Validated: 12M lookback momentum is the robust cross-sectional signal]
Tier 2: conviction meters for top-3 ranked sectors (DISPLAY ONLY - the rank is
        the decision driver; the TA/HMM score no longer gates Tier 3).
        Real HMM regime model (hmmlearn), real ADX/OBV on weekly OHLC.
Tier 3: deterministic stock picks by 26-week relative strength percentile vs
        the sector ETF. (Piotroski F-Score + TA score deferred.)
Exact API match with dashboard: tier1, tier2, tier3.
"""
import os
import json
import yfinance as yf
import pandas as pd
import numpy as np
from http.server import HTTPServer, SimpleHTTPRequestHandler
from datetime import datetime, timedelta

dashboard_path = os.path.join(os.path.dirname(__file__), "ns3_dashboard.html")
PORT = int(os.environ.get('PORT', 9237))

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

# 52w momentum needs >= 53 weekly bars; fetch 60 + 2 buffer.
LOOKBACK_WEEKS = 62
MOMENTUM_WEEKS = 52
RS_WEEKS = 26
RS_PERCENTILE = 0.75          # top 25% of holdings by 26w RS
HMM_BULL_THRESHOLD = 0.65
TOP_N = 3                     # sectors passing Tier 1
PIOTROSKI_MIN = 7             # minimum Piotroski F-Score to pass Tier 3 screen
TA_SCORE_MIN = 3              # minimum TA score for BUY in Tier 3
TIER3_TOP = 5                 # stocks returned per sector

_cache = {}
CACHE_TTL = 300

# ── HMM (guarded import: absent -> explicit "unavailable", never silent fake) ──
try:
    from hmmlearn.hmm import GaussianHMM
    HMM_AVAILABLE = True
except ImportError:
    GaussianHMM = None
    HMM_AVAILABLE = False


def fit_hmm_bull_prob(close: pd.Series):
    """2-state GaussianHMM on log returns -> P(currently in bull state) or None."""
    if not HMM_AVAILABLE:
        return None
    returns = np.log(close / close.shift(1)).dropna().values.reshape(-1, 1)
    if len(returns) < 20:
        return None
    import contextlib, io
    model = GaussianHMM(n_components=2, covariance_type="full",
                        n_iter=500, random_state=42)
    with contextlib.redirect_stderr(io.StringIO()):
        model.fit(returns)
    bull_state = int(np.argmax(model.means_.ravel()))
    posteriors = model.predict_proba(returns)
    return float(posteriors[-1, bull_state])


# ── Data: weekly OHLCV, 5-min TTL cache ──────────────────────────────────────

def get_weekly_ohlcv(symbols: list, weeks: int = LOOKBACK_WEEKS) -> dict:
    """Fetch weekly OHLCV for symbols. Returns {sym: DataFrame(o,h,l,c,v)}."""
    now = datetime.now()
    key = ("weekly", tuple(sorted(symbols)), weeks)
    if key in _cache:
        data, ts = _cache[key]
        if (now - ts).total_seconds() < CACHE_TTL:
            return data

    end = now.date()
    start = end - timedelta(weeks=weeks + 2)
    try:
        raw = yf.download(symbols, start=str(start), end=str(end), interval="1wk",
                          progress=False, auto_adjust=True, group_by="ticker")
    except Exception:
        return {}

    out = {}
    if raw is None or raw.empty:
        return {}
    if isinstance(raw.columns, pd.MultiIndex):
        for sym in symbols:
            if sym in raw.columns.get_level_values(0):
                df = raw[sym].dropna(subset=["Close"]).tail(weeks)
                out[sym] = pd.DataFrame({
                    "open": df["Open"], "high": df["High"],
                    "low": df["Low"], "close": df["Close"],
                    "volume": df["Volume"],
                }).dropna()
    else:  # single symbol download
        df = raw.dropna(subset=["Close"]).tail(weeks)
        out[symbols[0]] = pd.DataFrame({
            "open": df["Open"], "high": df["High"],
            "low": df["Low"], "close": df["Close"],
            "volume": df["Volume"],
        }).dropna()

    _cache[key] = (out, now)
    return out


def get_weekly_closes(symbols: list, weeks: int = LOOKBACK_WEEKS) -> pd.DataFrame:
    """Weekly close panel for symbols, oldest -> newest."""
    ohlcv = get_weekly_ohlcv(symbols, weeks)
    closes = pd.DataFrame({sym: df["close"] for sym, df in ohlcv.items()})
    return closes


# ── Indicators (weekly bars; Wilder RSI, real ADX/OBV) ──────────────────────

def calc_rsi(prices: pd.Series, period: int = 14) -> float:
    """Wilder's RSI."""
    if len(prices) < period + 1:
        return 50.0
    delta = prices.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    val = 100 - (100 / (1 + rs.iloc[-1]))
    return round(val, 1) if not pd.isna(val) else 50.0


def calc_macd(prices: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> float:
    """MACD histogram value (last bar)."""
    if len(prices) < slow + signal:
        return 0.0
    ema_fast = prices.ewm(span=fast).mean()
    ema_slow = prices.ewm(span=slow).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal).mean()
    return round(macd_line.iloc[-1] - signal_line.iloc[-1], 4)


def calc_adx(df: pd.DataFrame, period: int = 14) -> float:
    """Wilder's ADX on weekly OHLC."""
    h, l, c = df["high"], df["low"], df["close"]
    if len(df) < period * 2:
        return 25.0
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    up = c.diff().clip(lower=0)
    down = (-c.diff()).clip(lower=0)
    di_plus = 100 * up.ewm(alpha=1 / period, adjust=False).mean() / atr.replace(0, np.nan)
    di_minus = 100 * down.ewm(alpha=1 / period, adjust=False).mean() / atr.replace(0, np.nan)
    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / period, adjust=False).mean()
    val = adx.iloc[-1]
    return round(val, 1) if not pd.isna(val) else 25.0


def calc_obv(df: pd.DataFrame, window: int = 5) -> dict:
    """On-Balance Volume (true volume), slope over last N weeks."""
    c, v = df["close"], df["volume"]
    if len(c) < 2:
        return {'isRising': False, 'slope': 0}
    direction = np.sign(c.diff().fillna(0))
    obv = pd.Series(direction * v, index=c.index).cumsum()
    slope = float(obv.iloc[-1] - obv.iloc[-1 - window]) if len(obv) > window else 0.0
    return {'isRising': slope > 0, 'slope': round(slope, 1)}


# ── Tier 1: 52-week ratio momentum ranking ───────────────────────────────────

def run_tier1() -> dict:
    symbols = [s['symbol'] for s in SECTORS] + ['SPY']
    closes = get_weekly_closes(symbols)
    if closes.empty or 'SPY' not in closes.columns:
        return {"generatedAt": datetime.utcnow().isoformat() + "Z", "sectors": []}

    spy = closes['SPY']
    results = []
    for sector in SECTORS:
        sym = sector['symbol']
        if sym not in closes.columns:
            continue
        prices = closes[sym].dropna()
        aligned_spy = spy.reindex(prices.index).ffill()

        # 52-week ratio momentum vs SPY
        if len(prices) >= MOMENTUM_WEEKS + 1 and len(aligned_spy) >= MOMENTUM_WEEKS + 1:
            ratio = prices / aligned_spy
            momentum = round(((ratio.iloc[-1] / ratio.iloc[-1 - MOMENTUM_WEEKS]) - 1) * 100, 2)
        else:
            momentum = 0.0

        # YTD on weekly closes
        ytd_prices = prices[prices.index.year == datetime.now().year]
        ytd = round(((ytd_prices.iloc[-1] - ytd_prices.iloc[0]) / ytd_prices.iloc[0]) * 100, 2) \
            if len(ytd_prices) > 1 else 0.0

        results.append({
            "symbol": sym,
            "name": sector['name'],
            "momentum": momentum,
            "ytd": ytd,
            "currentPrice": round(float(prices.iloc[-1]), 2),
        })

    results.sort(key=lambda x: x['momentum'], reverse=True)
    for i, r in enumerate(results):
        r['rank'] = i + 1
        r['passToTier2'] = i < TOP_N  # rank-based pass; Tier 2 does NOT gate

    return {"generatedAt": datetime.utcnow().isoformat() + "Z", "sectors": results}


# ── Tier 2: conviction meters for top-3 (display only) ───────────────────────

def run_tier2() -> dict:
    tier1 = run_tier1()
    top3 = [s['symbol'] for s in tier1['sectors'] if s.get('passToTier2')]

    ohlcv = get_weekly_ohlcv(top3)
    etfs = []
    for i, sym in enumerate(top3):
        name = next((s['name'] for s in SECTORS if s['symbol'] == sym), sym)
        df = ohlcv.get(sym)
        if df is None or df.empty:
            continue
        prices = df["close"]

        macd_val = calc_macd(prices)
        rsi_val = calc_rsi(prices)
        adx_val = calc_adx(df)
        obv = calc_obv(df)
        bull_prob = fit_hmm_bull_prob(prices)
        hmm_bull = bull_prob is not None and bull_prob >= HMM_BULL_THRESHOLD

        # Conviction score (0-5) - displayed only; does not gate anything
        score = sum([hmm_bull, macd_val > 0, adx_val > 25, 40 <= rsi_val <= 70, obv['isRising']])
        if score >= 4:
            decision = f"STRONG {sym}"
        elif score >= 2:
            decision = "MODERATE"
        else:
            decision = f"WEAK {sym}"

        etfs.append({
            "symbol": sym,
            "name": name,
            "rank": i + 1,
            "currentPrice": round(float(prices.iloc[-1]), 2),
            "score": score,
            "maxScore": 5,
            "decision": decision,
            "hmm": {
                "bullProb": round(bull_prob, 4) if bull_prob is not None else None,
                "isGated": hmm_bull,
                "available": HMM_AVAILABLE and bull_prob is not None,
            },
            "macd": {"value": macd_val},
            "adx": {"value": adx_val},
            "rsi": {"value": rsi_val},
            "obv": obv,
            "holdings": [{"symbol": h, "name": h} for h in ETF_HOLDINGS.get(sym, [])],
        })

    return {"generatedAt": datetime.utcnow().isoformat() + "Z", "etfs": etfs}


# ── Tier 3: RS percentile → Piotroski F-Score → TA score (ported from PROD) ──

def piotroski_fscore(ticker_obj) -> tuple:
    """9-point Piotroski F-Score from yfinance fundamentals.
    Ported verbatim from NS-3_PROD/backend/main.py. Returns (score, breakdown)."""
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
    """26-week relative strength of stock vs its sector ETF (%).
    True 26-week window (PROD's version measures the full fetched window -
    fixed here to match the label and RS percentile semantics)."""
    aligned = stock_close.reindex(etf_close.index).ffill()
    ratio = (aligned / etf_close).dropna()
    if len(ratio) < RS_WEEKS + 1:
        return 0.0
    return ((ratio.iloc[-1] / ratio.iloc[-1 - RS_WEEKS]) - 1) * 100


def ta_score_stock(df: pd.DataFrame) -> tuple:
    """0-5 TA score for a stock: HMM (proper gate: bull_prob >= threshold),
    MACD, ADX, RSI, OBV. Ported from PROD; fixed hmm_ok to use probability."""
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    macd_val = calc_macd(c)
    rsi_val = calc_rsi(c)
    adx_val = calc_adx(df)
    obv = calc_obv(df)

    bull_prob = fit_hmm_bull_prob(c)
    hmm_ok = bull_prob is not None and bull_prob >= HMM_BULL_THRESHOLD
    macd_ok = macd_val > 0
    adx_ok = adx_val > 25
    rsi_ok = 45 <= rsi_val <= 75
    obv_ok = obv["isRising"]

    score = int(sum([hmm_ok, macd_ok, adx_ok, rsi_ok, obv_ok]))
    return score, {
        "hmmOk": hmm_ok,
        "hmmProb": round(bull_prob, 4) if bull_prob is not None else None,
        "macdOk": macd_ok,
        "adxOk": adx_ok,
        "rsiOk": rsi_ok,
        "obvOk": obv_ok,
    }


# per-ticker Piotroski cache (fundamentals fetch is slow; 5-min TTL)
_piotroski_cache = {}


def get_piotroski(sym: str) -> tuple:
    now = datetime.now()
    if sym in _piotroski_cache:
        val, ts = _piotroski_cache[sym]
        if (now - ts).total_seconds() < CACHE_TTL:
            return val
    try:
        val = piotroski_fscore(yf.Ticker(sym))
    except Exception as e:
        val = (0, {"error": str(e)})
    _piotroski_cache[sym] = (val, now)
    return val


def run_tier3() -> dict:
    tier1 = run_tier1()
    top3 = [s['symbol'] for s in tier1['sectors'] if s.get('passToTier2')]

    sectors = []
    for etf_sym in top3:
        holdings = ETF_HOLDINGS.get(etf_sym, [])
        if not holdings:
            continue
        tickers = [etf_sym] + holdings
        ohlcv = get_weekly_ohlcv(tickers)
        etf_close = ohlcv.get(etf_sym, pd.DataFrame())["close"] if etf_sym in ohlcv else pd.Series()
        if etf_close.empty:
            sectors.append({"etf": etf_sym, "name": etf_sym, "stocks": []})
            continue

        # 1) rank holdings by 26-week relative strength vs the sector ETF
        rs_scores = {}
        for sym in holdings:
            df = ohlcv.get(sym)
            if df is None or df.empty:
                continue
            sc = df["close"].reindex(etf_close.index).ffill()
            rs_scores[sym] = round(relative_strength_26w(sc, etf_close), 2)

        if not rs_scores:
            sectors.append({"etf": etf_sym, "name": etf_sym, "stocks": []})
            continue

        rs_series = pd.Series(rs_scores).sort_values(ascending=False)
        cutoff = max(1, int(len(rs_series) * (1 - RS_PERCENTILE)))
        top_rs = rs_series.index[:cutoff]

        # 2) Piotroski F-Score screen on the RS leaders
        f_scores = {sym: get_piotroski(sym) for sym in top_rs}
        passed_f = [s for s in top_rs if f_scores[s][0] >= PIOTROSKI_MIN]
        if len(passed_f) < 2:  # fallback: top-3 by F-Score (PROD behavior)
            passed_f = sorted(top_rs, key=lambda s: f_scores[s][0], reverse=True)[:3]

        # 3) TA score + HMM gate -> decision, confidence
        stocks = []
        for sym in passed_f:
            df = ohlcv.get(sym)
            if df is None or df.empty:
                continue
            score, ta = ta_score_stock(df)
            hmm_gated = ta["hmmOk"]
            if not hmm_gated:
                decision = "AVOID"
            elif score >= TA_SCORE_MIN:
                decision = "BUY"
            elif score == 2:
                decision = "WATCH"
            else:
                decision = "AVOID"
            hmm_prob = ta["hmmProb"] if ta["hmmProb"] is not None else 0.5
            confidence = round(hmm_prob * (score / 5), 4)
            stocks.append({
                "symbol": sym,
                "name": sym,
                "decision": decision,
                "confidence": confidence,
                "rs26w": rs_scores[sym],
                "fscore": f_scores[sym][0],
                "fscoreBreakdown": f_scores[sym][1],
                "taScore": score,
                "taBreakdown": ta,
            })

        stocks.sort(key=lambda x: x["confidence"], reverse=True)
        stocks = stocks[:TIER3_TOP]

        sectors.append({
            "etf": etf_sym,
            "name": next((s['name'] for s in SECTORS if s['symbol'] == etf_sym), etf_sym),
            "stocks": stocks,
        })

    return {"generatedAt": datetime.utcnow().isoformat() + "Z", "sectors": sectors}


# ── HTTP layer ───────────────────────────────────────────────────────────────

class NS3Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    def _json(self, code, data):
        self.send_response(code)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        # Convert numpy types to native Python for JSON serialization
        self.wfile.write(json.dumps(data, default=lambda o: int(o) if isinstance(o, (np.bool_, np.integer)) else float(o) if isinstance(o, np.floating) else str(o)).encode())

    def do_GET(self):
        if self.path in ('/', '/ns3_dashboard.html'):
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            with open(dashboard_path, 'rb') as f:
                self.wfile.write(f.read())
            return

        if self.path == '/health':
            return self._json(200, {"status": "ok", "service": "ns3-qa"})

        if self.path == '/api/v1/tier1':
            return self._json(200, run_tier1())

        if self.path == '/api/v1/tier2':
            return self._json(200, run_tier2())

        if self.path == '/api/v1/tier3':
            return self._json(200, run_tier3())

        self.send_response(404)
        self.end_headers()


if __name__ == '__main__':
    os.chdir(os.path.dirname(__file__))
    server = HTTPServer(('0.0.0.0', PORT), NS3Handler)
    print(f"NS-3 QA running on port {PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()
