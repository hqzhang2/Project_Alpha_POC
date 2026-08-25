"""pipeline.py — NS-8 Data Pipeline.

Orchestrates: fetch prices → generate signals → tranche scheduling → persist.
"""
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import config
import signals
import store
import vol


def _daily_returns_from_closes(
    closes_by_ticker: Dict[str, List[float]],
    window: int = 61,
) -> Dict[str, List[float]]:
    """Derive daily returns (oldest-first) from daily closes (oldest-first).

    Uses the trailing `window + 1` closes per ticker — enough observations for
    the EWMA ex-ante vol (`vol.exante_vol`). No lookahead: only closes at or
    before as_of are ever passed in by `run_refresh`. Mirrors the shape of
    walkforward._daily_returns_upto so live sizing == validated sizing.
    """
    out: Dict[str, List[float]] = {}
    for t, closes in (closes_by_ticker or {}).items():
        tail = list(closes)[-(window + 1):]
        rets = []
        for prev, cur in zip(tail, tail[1:]):
            if prev:
                rets.append(cur / prev - 1.0)
        if len(rets) >= 3:          # ewma_var needs >= 3 obs to estimate
            out[t] = rets
    return out


def fetch_prices_yfinance(
    tickers: List[str],
    lookback_days: int,
    end_date: str = None
) -> Dict[str, List[float]]:
    """Fetch daily adjusted closes from yfinance.

    Args:
        tickers: List of ticker symbols.
        lookback_days: How many calendar days to fetch.
        end_date: End date (YYYY-MM-DD). Default: today.

    Returns:
        {ticker: [daily adjusted closes oldest-first]}
    """
    import yfinance as yf

    end = datetime.strptime(end_date, "%Y-%m-%d") if end_date else datetime.now()
    start = end - timedelta(days=lookback_days + 30)  # buffer for weekends/holidays

    data = yf.download(
        tickers,
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        auto_adjust=True,
        progress=False,
        group_by="ticker"
    )

    closes = {}
    if len(tickers) == 1:
        # Single ticker returns a DataFrame, not a MultiIndex
        ticker = tickers[0]
        series = data["Close"].dropna()
        if series is not None:
            closes[ticker] = series.tolist()
    else:
        # Multiple tickers returns MultiIndex columns
        if hasattr(data.columns, 'levels'):
            for ticker in tickers:
                if ticker in data.columns.levels[0]:
                    series = data[ticker]["Close"].dropna()
                    if series is not None:
                        closes[ticker] = series.tolist()

    return closes


def fetch_prices(
    tickers: List[str],
    lookback_days: int,
    end_date: str = None,
    source: str = None
) -> Dict[str, List[float]]:
    """Fetch prices from configured source."""
    source = source or config.DATA_SOURCE
    if source == "yfinance":
        return fetch_prices_yfinance(tickers, lookback_days, end_date)
    elif source == "polygon":
        raise NotImplementedError("Polygon.io source not yet implemented")
    else:
        raise ValueError(f"Unknown data source: {source}")


def get_tranche_rebalance_dates(
    as_of: str,
    tranches: int = 4
) -> List[str]:
    """Compute next rebalance date for each tranche.

    Tranche 0 rebalances week 1 of month, tranche 1 week 2, etc.
    Returns list of YYYY-MM-DD dates.
    """
    dt = datetime.strptime(as_of, "%Y-%m-%d")
    # Find first day of next month
    if dt.month == 12:
        next_month = dt.replace(year=dt.year + 1, month=1, day=1)
    else:
        next_month = dt.replace(month=dt.month + 1, day=1)

    dates = []
    for i in range(tranches):
        # Week i+1 of next month (approximate: day 1 + i*7)
        rebalance_day = min(1 + i * 7, 28)
        try:
            rebalance_date = next_month.replace(day=rebalance_day)
        except ValueError:
            # Handle months with < 28 days
            rebalance_date = next_month.replace(day=28)
        dates.append(rebalance_date.strftime("%Y-%m-%d"))

    return dates


def run_refresh(
    as_of: Optional[str] = None,
    source: str = None,
    tickers: Optional[List[str]] = None
) -> Dict:
    """One full pipeline pass: fetch → signal → tranche → persist.

    Args:
        as_of: Date to compute signals for (YYYY-MM-DD). Default: today.
        source: Data source ("yfinance" or "polygon").
        tickers: Optional override of universe.

    Returns:
        Signal document dict.
    """
    as_of = as_of or datetime.now().strftime("%Y-%m-%d")
    source = source or config.DATA_SOURCE
    tickers = tickers or (config.RISKY_ASSETS + [config.CASH_PROXY])

    # Initialize DB
    store.init_db()
    store.init_tranche_state()

    # 1. Fetch prices
    prices = fetch_prices(tickers, config.LOOKBACK_DAYS, as_of, source)

    # 2. Generate signals (risky assets only)
    risky_prices = {t: prices[t] for t in config.RISKY_ASSETS if t in prices}
    sigs = signals.generate_signals(risky_prices)

    # v4.7: size with the SAME function the walk-forward harness validates
    # (live == validated). inverse-vol needs ex-ante vols from DAILY RETURNS,
    # so derive returns from closes first (the live path previously had no
    # closes→returns step — see research_ns8_feed_v47.md §4 CRITICAL detail).
    if config.SIZING_METHOD == "inverse_vol":
        rets = _daily_returns_from_closes(
            risky_prices, window=config.VOL_RETURN_WINDOW)
        vols: Optional[Dict[str, Optional[float]]] = {
            t: vol.exante_vol(rets.get(t, [])) for t in config.RISKY_ASSETS}
        weights = signals.compute_weights_inverse_vol(sigs, vols)
    else:  # "fixed" (v1 legacy, explicitly configured)
        vols = None
        weights = signals.compute_weights(sigs)

    # 3. Tranche scheduling
    tranche_dates = get_tranche_rebalance_dates(as_of, config.TRANCHES)
    current_tranche = store.get_current_tranche(as_of)

    # Update tranche state
    tranche_state = store.get_tranche_state()
    for i, next_date in enumerate(tranche_dates):
        last_date = tranche_state[i]["last_rebalance"] if i < len(tranche_state) and tranche_state[i]["last_rebalance"] else as_of
        store.update_tranche_rebalance(i, next_date, last_date)

    # Mark current tranche as rebalanced
    store.update_tranche_rebalance(current_tranche, tranche_dates[current_tranche], as_of)

    # 4. Build and persist document
    doc = signals.build_signal_document(as_of, sigs, weights, version=1,
                                        vols=vols if config.SIZING_METHOD == "inverse_vol" else None)
    doc["tranche"] = {
        "current": current_tranche,
        "schedule": tranche_dates,
        "total": config.TRANCHES
    }

    store.upsert_signal(
        as_of, sigs, weights, doc["version"], doc["generated_at"]
    )
    # v4.7: export the ENRICHED document (feed-contract metadata lives only in
    # the doc — the ns8_signals table has fixed columns). export_signals_json
    # mirrors the full doc to data/signals.json AND strategy_output (JSONB).
    store.export_signals_json(doc=doc)

    return doc


if __name__ == "__main__":
    import sys
    as_of = sys.argv[1] if len(sys.argv) > 1 else None
    source = sys.argv[2] if len(sys.argv) > 2 else "yfinance"
    doc = run_refresh(as_of=as_of, source=source)
    print(json.dumps(doc, indent=2, default=str))