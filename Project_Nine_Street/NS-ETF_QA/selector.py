"""NS-ETF selector — unified signal scoring (spec §3).

Momentum core from NS-1's factor composite + NS-3-style weekly
sector-vs-SPY ranking. Deterministic end-to-end; no randomness anywhere.
"""
import math

import config
import indicators


def _safe(fn, *a, **kw):
    """None-safe wrapper: missing data → None, never a silent default."""
    try:
        return fn(*a, **kw)
    except Exception:
        return None


def momentum_blend(closes, windows=None):
    """Blended multi-window momentum: mean of per-window returns.
    Windows shorter than available data are skipped; all-missing → None."""
    windows = windows or config.MOMENTUM_WINDOWS
    rets = []
    for w in sorted(windows):
        if len(closes) > w:
            rets.append(closes[-1] / closes[-1 - w] - 1.0)
    if not rets:
        return None
    return sum(rets) / len(rets)


def risk_adjusted_momentum(closes, lookback=63):
    """Return over `lookback` divided by realized vol (annualized-ish)."""
    if len(closes) < lookback + 1:
        return None
    rets = [closes[i] / closes[i - 1] - 1.0 for i in range(len(closes) - lookback, len(closes))]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    sd = math.sqrt(var)
    if sd == 0:
        return None
    return sum(rets) / sd


def spy_relative_strength(closes, spy_closes, window=126):
    """Ticker return minus SPY return over the same window."""
    if len(closes) < window + 1 or len(spy_closes) < window + 1:
        return None
    return ((closes[-1] / closes[-1 - window]) -
            (spy_closes[-1] / spy_closes[-1 - window]))


def score_ticker(conn, ticker, spy_closes):
    """Composite score for one ticker. Returns dict with component breakdown
    or {'error': ...} when data is insufficient (surfaced, never defaulted)."""
    closes = store_series(conn, ticker)
    if closes is None or len(closes) < max(config.MOMENTUM_WINDOWS) // 2 + 5:
        return {"ticker": ticker, "error": "insufficient price history"}

    mom = _safe(momentum_blend, closes)
    ram = _safe(risk_adjusted_momentum, closes)
    rs = _safe(spy_relative_strength, closes, spy_closes)
    rsi = _safe(indicators.wilder_rsi, closes, config.RSI_PERIOD)
    bb = _safe(indicators.bollinger_position, closes,
               config.BB_PERIOD, config.BB_STD)
    highs = store_series(conn, ticker, field="high")
    lows = store_series(conn, ticker, field="low")
    adx = None
    if highs and lows:
        adx = _safe(indicators.wilder_adx, highs, lows, closes, config.ADX_PERIOD)

    # Composite: momentum-heavy, RSI supplementary only (house convention).
    parts, weights = [], []
    if mom is not None:
        parts.append(100.0 * mom); weights.append(1.0)
    if ram is not None:
        parts.append(10.0 * ram); weights.append(0.8)
    if rs is not None:
        parts.append(100.0 * rs); weights.append(0.6)
    # RSI as supplementary: rewards mid-range strength, penalizes extremes
    if rsi is not None:
        parts.append((50.0 - abs(rsi - 55.0)) * 0.2); weights.append(0.15)
    # ADX z-score proxy vs its own series: trend-strength confirmation
    if isinstance(adx, dict) and adx.get("adx") is not None:
        ser = adx.get("adx_series") or []
        if len(ser) >= 20:
            mu = sum(ser[-60:]) / min(60, len(ser))
            parts.append((adx["adx"] - mu)); weights.append(0.25)

    if not weights:
        return {"ticker": ticker, "error": "no computable components"}

    score = sum(p * w for p, w in zip(parts, weights)) / sum(weights)
    return {
        "ticker": ticker,
        "score": round(score, 4),
        "components": {
            "momentum_blend": mom, "risk_adj_momentum": ram,
            "spy_rel_strength": rs, "rsi": rsi,
            "bb_position": bb,
            "adx": adx["adx"] if isinstance(adx, dict) else None,
        },
    }


def rank_sleeve(conn, sleeve_tickers, spy_closes):
    """Score + rank a sleeve's tickers. Returns list of scored dicts,
    best first. Error rows carry 'error' and sort last."""
    scored = [score_ticker(conn, t, spy_closes) for t in sleeve_tickers]
    ok = [s for s in scored if "score" in s]
    bad = [s for s in scored if "error" in s]
    ok.sort(key=lambda s: s["score"], reverse=True)
    return ok + bad


def inverse_vol_weights(conn, tickers, lookback=63):
    """Inverse-volatility weights within a sleeve (NS-1 pattern).
    Missing-data names get the minimum weight share; never crash."""
    vols = {}
    for t in tickers:
        closes = store_series(conn, t)
        if closes and len(closes) >= lookback + 1:
            rets = [closes[i] / closes[i - 1] - 1.0
                    for i in range(len(closes) - lookback, len(closes))]
            var = sum(r * r for r in rets) / len(rets)
            vols[t] = math.sqrt(var) if var > 0 else None
        else:
            vols[t] = None
    known = {t: v for t, v in vols.items() if v}
    if not known:
        eq = 1.0 / len(tickers)
        return {t: eq for t in tickers}
    inv = {t: 1.0 / v for t, v in known.items()}
    tot = sum(inv.values())
    raw = {t: inv[t] / tot for t in inv}
    n_missing = len(tickers) - len(raw)
    floor = 0.5 / len(tickers) if n_missing else 0.0   # reserve half-share for unknowns
    scaled = {t: w * (1.0 - floor * n_missing) for t, w in raw.items()}
    for t, v in vols.items():
        if v is None:
            scaled[t] = floor
    return scaled


def store_series(conn, ticker, field="close"):
    rows = conn.execute(
        f"SELECT {field} FROM prices WHERE ticker=? ORDER BY date",
        (ticker,)).fetchall()
    vals = [r[0] for r in rows if r[0] is not None]
    return vals or None
