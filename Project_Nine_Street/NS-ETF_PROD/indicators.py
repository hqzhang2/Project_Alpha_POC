"""NS-ETF indicators — standard Wilder ADX, RSI, Bollinger position.

The ADX here is the canonical Wilder implementation (smoothed +DM/-DM,
DX, ADX), fixing the NS-4 QA proxy and the NS-4 PROD scaling bug
(tr14 = ATR*14 mixed a rolling MEAN of TR with rolling SUMS of DM).
Unit-tested against hand-computed fixtures in tests/test_selector.py.
"""
import math


def wilder_adx(highs, lows, closes, period=14):
    """Standard Wilder ADX. Returns dict {+DI, -DI, DX, ADX} or None if
    insufficient data. Lists are oldest-first equal-length series."""
    n = len(closes)
    if n < 2 * period + 1:
        return None

    trs, pdms, mdms = [], [], []
    for i in range(1, n):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i] - closes[i - 1]))
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        pdm = up if (up > down and up > 0) else 0.0
        mdm = down if (down > up and down > 0) else 0.0
        trs.append(tr)
        pdms.append(pdm)
        mdms.append(mdm)

    # Wilder smoothing: first value = SUM of first `period`, then
    # prev - prev/period + current (this is the step NS-4 PROD got wrong).
    def wilder_smooth(vals):
        out = [sum(vals[:period])]
        for v in vals[period:]:
            out.append(out[-1] - out[-1] / period + v)
        return out

    str_s, spdms, smdms = wilder_smooth(trs), wilder_smooth(pdms), wilder_smooth(mdms)

    dxs, plus_di_s, minus_di_s = [], [], []
    for tr_s, p_s, m_s in zip(str_s, spdms, smdms):
        if tr_s <= 0:
            dxs.append(0.0)
            plus_di_s.append(0.0)
            minus_di_s.append(0.0)
            continue
        pdi = 100.0 * p_s / tr_s
        mdi = 100.0 * m_s / tr_s
        plus_di_s.append(pdi)
        minus_di_s.append(mdi)
        denom = pdi + mdi
        dxs.append(100.0 * abs(pdi - mdi) / denom if denom > 0 else 0.0)

    # ADX = Wilder-smoothed DX over `period` DX values
    adxs = [sum(dxs[:period]) / period]
    for d in dxs[period:]:
        adxs.append((adxs[-1] * (period - 1) + d) / period)

    return {
        "plus_di": plus_di_s[-1],
        "minus_di": minus_di_s[-1],
        "dx": dxs[-1],
        "adx": adxs[-1],
        "adx_series": adxs,
    }


def wilder_rsi(closes, period=14):
    """Wilder RSI (Smoothed MA of gains/losses). None if insufficient data."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0
    rs = ag / al
    return 100.0 - 100.0 / (1.0 + rs)


def bollinger_position(closes, period=20, num_std=2.0):
    """Where the last close sits inside the Bollinger band, normalized to
    [0, 1]: 0 = lower band, 1 = upper band. None if insufficient data."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    mid = sum(window) / period
    var = sum((c - mid) ** 2 for c in window) / period
    sd = math.sqrt(var)
    if sd == 0:
        return 0.5
    upper, lower = mid + num_std * sd, mid - num_std * sd
    return (closes[-1] - lower) / (upper - lower)


def sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema_series(values, span):
    """EMA series (same length as input; seeded with SMA of first `span`)."""
    if len(values) < span:
        return None
    alpha = 2.0 / (span + 1)
    out = [None] * (span - 1)
    prev = sum(values[:span]) / span
    out.append(prev)
    for v in values[span:]:
        prev = alpha * v + (1 - alpha) * prev
        out.append(prev)
    return out


def macd(closes, fast=12, slow=26, signal=9):
    """Returns dict {macd, signal, hist} or None."""
    if len(closes) < slow + signal:
        return None
    ef, es = ema_series(closes, fast), ema_series(closes, slow)
    if ef is None or es is None:
        return None
    macd_line = [f - s for f, s in zip(ef[slow - 1:], es[slow - 1:])]
    sig = ema_series(macd_line, signal)
    if sig is None:
        return None
    last = len(sig) - 1
    return {"macd": macd_line[last], "signal": sig[last],
            "hist": macd_line[last] - sig[last]}
