"""signals.py — NS-8 Signal Generation Logic.

Pure functions for SMA computation and binary signal generation.
No I/O, no external dependencies beyond stdlib.
"""
from typing import Dict, List, Optional

import config


def compute_sma(closes: List[float], window: Optional[int] = None) -> Optional[float]:
    """Simple moving average on daily closes (oldest first).

    Args:
        closes: List of daily adjusted closes, oldest first.
        window: SMA window (default: config.SMA_WINDOW).

    Returns:
        SMA value or None if insufficient history.
    """
    window = window or config.SMA_WINDOW
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def generate_signals(
    prices: Dict[str, List[float]],
    window: Optional[int] = None
) -> Dict[str, int]:
    """Return {ticker: 1|0} binary signal at month-end.

    Args:
        prices: {ticker: [daily closes oldest-first]} for RISKY_ASSETS only.
        window: SMA window (default: config.SMA_WINDOW).

    Returns:
        Binary signals: 1 = long, 0 = cash.
    """
    window = window or config.SMA_WINDOW
    signals = {}
    for ticker, closes in prices.items():
        sma = compute_sma(closes, window)
        if sma is None:
            signals[ticker] = 0  # insufficient history → cash
        else:
            signals[ticker] = 1 if closes[-1] > sma else 0
    return signals


def compute_weights(signals: Dict[str, int]) -> Dict[str, float]:
    """20% per signal=1, remainder to SHV.

    Args:
        signals: {ticker: 1|0} from generate_signals().

    Returns:
        Weights dict including SHV, summing to 1.0.
    """
    weights = {}
    risky_on = sum(1 for v in signals.values() if v == 1)

    for ticker, sig in signals.items():
        if ticker == config.CASH_PROXY:
            continue
        weights[ticker] = config.ASSET_WEIGHT if sig == 1 else 0.0

    weights[config.CASH_PROXY] = round(1.0 - sum(weights.values()), 12)
    return weights


def compute_weights_inverse_vol(signals: Dict[str, int],
                                vols: Dict[str, Optional[float]]) -> Dict[str, float]:
    """Inverse-vol weights within the in-trend set, scaled by the in-trend count.

    Preserves the long/flat capital-preservation property: total risky exposure
    = ASSET_WEIGHT × N_in_trend (so the book scales down toward cash as trends
    break), while the in-trend allocation is risk-parity (∝ 1/σ) rather than
    equal-weight. This is the long/flat analogue of MOP's 1/σ sizing that does
    NOT re-normalize to 100% (which would concentrate risk when few assets are
    in-trend).

    Fail-open: an in-trend asset with a missing/None vol is treated as out of
    trend (weight 0); if no in-trend asset has a valid vol the book goes to cash.
    """
    inv_vol = {}
    for t, s in signals.items():
        if s == 1 and t != config.CASH_PROXY:
            v = vols.get(t)
            if v:                       # truthy -> valid, non-zero vol
                inv_vol[t] = 1.0 / v
    total = sum(inv_vol.values())
    weights = {t: 0.0 for t in signals if t != config.CASH_PROXY}
    if total > 0:
        # scale by the count of assets with a VALID vol only (conservative:
        # an in-trend asset whose vol can't be estimated is treated as not
        # held, so the book holds proportionally more cash, never more risk).
        scale = config.ASSET_WEIGHT * len(inv_vol)
        for t, iv in inv_vol.items():
            weights[t] = scale * (iv / total)
    weights[config.CASH_PROXY] = round(1.0 - sum(weights.values()), 12)
    return weights


def generate_signals_sign12m(prices: Dict[str, List[float]],
                             window_days: int = 252) -> Dict[str, int]:
    """Long if trailing 12-month return > 0, else cash (MOP canonical signal).

    Args:
        prices: {ticker: [daily closes oldest-first]} for RISKY_ASSETS only.
        window_days: trailing lookback (default 252 trading days ~ 12 months).

    Returns:
        Binary signals: 1 = long, 0 = cash.
    """
    sigs = {}
    for t, closes in prices.items():
        if len(closes) >= window_days and closes[-1] > closes[-window_days]:
            sigs[t] = 1
        else:
            sigs[t] = 0
    return sigs


def build_signal_document(
    as_of: str,
    signals: Dict[str, int],
    weights: Dict[str, float],
    version: int = 1
) -> Dict:
    """Build the full signal document for persistence/API."""
    from datetime import datetime
    return {
        "as_of": as_of,
        "signals": signals,
        "weights": weights,
        "version": version,
        "generated_at": datetime.now().isoformat(timespec="seconds")
    }


def is_month_end(date_str: str) -> bool:
    """Check if date is month-end (last calendar day).

    In production, this should check last *trading* day.
    For now, simple calendar check.
    """
    from datetime import datetime, timedelta
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    next_day = dt + timedelta(days=1)
    return next_day.month != dt.month