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

    weights[config.CASH_PROXY] = 1.0 - sum(weights.values())
    return weights


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