"""
price_feed.py — NS-6 daily EOD price feed (R2a).

Bridges the gap the v2 full-stack review flagged as the core deliverable: the
drawdown engine read `current_dd=0.0` because no price pipeline fed it. This
module fetches daily closes for the cockpit's selected portfolio (+ SPY, + VIX),
computes real drawdown/budget/remaining/multiplier, and persists one row to
`store.upsert_drawdown()` so `/api/enforcement/status` surfaces live numbers.

Design (matches the stack's house rules):
- Pure + fail-open: no input → no crash, no fake data. If prices can't be
  fetched, we do NOT write a row (a row is only written from REAL prices), so
  enforcement's `data_stale` flag fires rather than silently showing 0.0
  (sibling of the "fake valid default" bug class).
- Holdings resolution is shared with qa_server via `resolve_holdings()` (DRY —
  the shares→weights normalization that once silently exploded must live in
  exactly one place).
- All drawdown/budget values are NEGATIVE fractions; `budget.py` owns the math
  (sign-corrected; the floor guarantee uses min()).
- yfinance is already in the stack. Prices are cached to `data/ns6_prices.pkl`
  (tz-naive) for offline reproducibility/debug; the live run always fetches
  fresh and merges only missing tickers into the cache.
"""

import json
import logging
from datetime import date as date_cls
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import budget as budget_mod
import config
import enforcement as enforcement_mod
import store

log = logging.getLogger("ns6.price_feed")

DATA_DIR = Path(__file__).resolve().parent / "data"
PRICES_CACHE = DATA_DIR / "ns6_prices.pkl"

# NS-5 portfolio store path (decoupled file read — same derivation as qa_server).
NS5_PORTFOLIOS_PATH = (
    Path(__file__).resolve().parent.parent / "NS-5_QA" / "data" / "portfolios.json"
)

# Non-equity / benchmark / vix tickers always fetched (not portfolio holdings).
SPY_TICKER = "SPY"
VIX_TICKER = "^VIX"


# ── Holdings resolution (shared with qa_server._portfolio_holdings) ───────
def load_ns5_portfolios(path: Optional[Path] = None) -> Dict[str, dict]:
    """Raw NS-5 portfolio holdings {name: {ticker: shares}}. Fail-open."""
    path = path or NS5_PORTFOLIOS_PATH
    try:
        if path.exists():
            with open(path) as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        log.warning("read NS-5 portfolios failed: %s", exc)
    return {}


def resolve_holdings(
    active: str, source: str, ns5_portfolios: Dict[str, dict]
) -> Tuple[str, bool, Dict[str, float], Dict[str, float]]:
    """Resolve the cockpit's current portfolio holdings + source info.

    PURE (no I/O). Returns (source_name, is_model, weights, shares).
    - Real NS-5 portfolio name present in `ns5_portfolios` -> holdings
      normalized to WEIGHTS (shares / total shares) for the engine,
      is_model=False; `shares` = raw NS-5 shares (for the modal).
    - "model" or a vanished name -> active profile's model portfolio
      (already weights), is_model=True; `shares` = {}.
    """
    if source != store.MODEL_SOURCE:
        if source in ns5_portfolios:
            raw = ns5_portfolios[source]
            shares: Dict[str, float] = {}
            for tk, v in raw.items():
                if isinstance(v, dict):
                    shares[str(tk).upper()] = float(v.get("shares", 0))
                else:
                    shares[str(tk).upper()] = float(v)
            total = sum(shares.values())
            weights = {tk: sh / total for tk, sh in shares.items()} if total else {}
            return source, False, weights, shares
        log.warning("portfolio '%s' not in NS-5 store; using model", source)
    return active, True, config.model_portfolio(active), {}


def current_holdings() -> Tuple[str, bool, Dict[str, float], Dict[str, float]]:
    """Resolve the persisted cockpit selection (batch-job convenience)."""
    active = store.get_active_profile()
    source = store.get_portfolio_source()
    return resolve_holdings(active, source, load_ns5_portfolios())


# ── Price fetching ─────────────────────────────────────────────────────────
def _load_cache() -> Dict[str, "pd.Series"]:
    try:
        if PRICES_CACHE.exists():
            import pandas as pd

            return pd.read_pickle(PRICES_CACHE)
    except Exception as exc:  # noqa: BLE001
        log.warning("read price cache failed: %s", exc)
    return {}


def _save_cache(cache: Dict[str, "pd.Series"]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        import pandas as pd

        pd.to_pickle(cache, PRICES_CACHE)
    except Exception as exc:  # noqa: BLE001
        log.warning("write price cache failed: %s", exc)


def _fetch_close(ticker: str, period: str = "2y") -> Optional["pd.Series"]:
    """Daily Close series for a ticker (tz-naive index), or None on failure."""
    try:
        import yfinance as yf

        df = yf.Ticker(ticker).history(period=period, auto_adjust=False)
        if df is None or df.empty or "Close" not in df:
            return None
        s = df["Close"].astype(float)
        if getattr(s.index, "tz", None) is not None:
            s.index = s.index.tz_localize(None)  # normalize to tz-naive (skill pitfall)
        return s
    except Exception as exc:  # noqa: BLE001
        log.warning("fetch %s failed: %s", ticker, exc)
        return None


def fetch_prices(tickers: List[str], period: str = "2y") -> Dict[str, "pd.Series"]:
    """Fetch Close series for each ticker, merging missing ones into the cache.

    Returns only the tickers that actually have data (fail-open). The live run
    always fetches fresh; the cache is a fallback/debug artifact.
    """
    cache = _load_cache()
    out: Dict[str, "pd.Series"] = {}
    for tk in tickers:
        series = _fetch_close(tk, period)
        if series is not None and len(series) > 0:
            out[tk] = series
            cache[tk] = series  # merge into cache
        elif tk in cache:
            log.warning("live fetch failed for %s; using cached series", tk)
            out[tk] = cache[tk]
    _save_cache(cache)
    return out


# ── Drawdown / budget computation ──────────────────────────────────────────
def _align(closes: Dict[str, "pd.Series"], weights: Dict[str, float]):
    """Intersect Close series on a common date index; renormalize weights to
    the tickers present. Returns (dates, aligned_matrix, weights_subset)."""
    import pandas as pd

    present = [tk for tk in weights if tk in closes and len(closes[tk]) > 0]
    if not present:
        return None, None, None
    frame = pd.DataFrame({tk: closes[tk] for tk in present}).dropna()
    if len(frame) < 2:
        return None, None, None
    sub = {tk: weights[tk] for tk in present}
    tot = sum(sub.values()) or 1.0
    sub = {tk: w / tot for tk, w in sub.items()}
    return frame.index, frame, sub


def _portfolio_nav_series(
    closes: Dict[str, "pd.Series"], weights: Dict[str, float], shares: Dict[str, float]
) -> List[float]:
    """NAV series (floats) for the portfolio.

    - shares path (NS-5 portfolio): NAV_t = Σ shares_i × price_{i,t}
      (absolute-dollar NAV — drawdown is scale-invariant so a base of $0 works).
    - weights path (model): daily return r_t = Σ w_i × r_{i,t}, chained from 1.0.
    """
    import pandas as pd

    if shares:
        frame = pd.DataFrame(
            {tk: closes[tk] for tk in shares if tk in closes and len(closes[tk]) > 0}
        ).dropna()
        if len(frame) < 2:
            return []
        nav = (frame * pd.Series(shares).loc[frame.columns]).sum(axis=1)
        return [float(x) for x in nav.tolist()]

    idx, frame, sub = _align(closes, weights)
    if idx is None:
        return []
    rets = frame.pct_change().dropna()
    if rets.empty:
        return []
    w = pd.Series(sub)
    daily_r = (rets * w).sum(axis=1)
    nav = (1.0 + daily_r).cumprod()
    return [1.0] + [float(x) for x in nav.tolist()]


def compute_snapshot(
    weights: Dict[str, float],
    shares: Dict[str, float],
    closes: Dict[str, "pd.Series"],
    theta=None,
) -> Dict:
    """Full budget snapshot dict from price data (reuses budget.status_snapshot
    where possible). Fail-open: no data -> all zeros/1.0 (no drawdown = no risk),
    but callers must NOT persist a row they cannot support with real prices."""
    theta = theta or config.load_profile(store.get_active_profile())[0]
    portfolio_prices = _portfolio_nav_series(closes, weights, shares)

    spy = closes.get(SPY_TICKER)
    spy_prices = [float(x) for x in spy.tolist()] if spy is not None else []
    spy_dd = budget_mod.compute_spy_drawdown(spy_prices)

    current_dd = budget_mod.compute_drawdown(portfolio_prices)
    budget = budget_mod.compute_budget(spy_dd, theta)
    remaining = budget_mod.budget_remaining(current_dd, budget, theta)
    multiplier = enforcement_mod.compute_exposure_multiplier(remaining, theta)

    vix = closes.get(VIX_TICKER)
    latest_vix = float(vix.iloc[-1]) if vix is not None and len(vix) else None

    return {
        "current_drawdown_pct": round(current_dd, 4),
        "spy_drawdown_pct": round(spy_dd, 4),
        "budget_pct": round(budget, 4),
        "budget_remaining_pct": round(remaining, 4),
        "exposure_multiplier": round(multiplier, 4),
        "latest_vix": latest_vix,
        "n_tickers": len(closes),
        "as_of": str(closes[max(closes, key=lambda k: len(closes[k]))].index[-1].date())
        if closes else None,
    }


# ── Batch entrypoint ───────────────────────────────────────────────────────
def run_once(theta=None) -> Optional[Dict]:
    """Resolve holdings, fetch prices, compute snapshot, persist one row.

    Returns the snapshot dict, or None if nothing could be computed (no fake
    data written). Designed for a launchd daily cron (R2a)."""
    source, is_model, weights, shares = current_holdings()
    tickers = list(weights.keys()) + [SPY_TICKER]
    theta = theta or config.load_profile(store.get_active_profile())[0]
    if theta.get("price_feed", {}).get("fetch_vix", True):
        tickers.append(VIX_TICKER)

    closes = fetch_prices(tickers, period=theta.get("price_feed", {}).get("period", "2y"))
    snap = compute_snapshot(weights, shares, closes, theta)

    # No price data at all -> do NOT write a fake row; enforcement flags stale.
    if not closes or snap["as_of"] is None:
        log.warning("price feed: no live prices; no row written (enforcement will flag stale)")
        return None

    store.upsert_drawdown(
        snap["as_of"], snap["spy_drawdown_pct"], snap["current_drawdown_pct"],
        snap["budget_pct"], snap["budget_remaining_pct"], snap["exposure_multiplier"],
    )
    log.info("price feed upserted %s: dd=%.4f spy=%.4f budget=%.4f remaining=%.2f",
             snap["as_of"], snap["current_drawdown_pct"], snap["spy_drawdown_pct"],
             snap["budget_pct"], snap["budget_remaining_pct"])
    return snap


def is_stale(latest_row: Optional[Dict], staleness_days: int) -> Tuple[bool, Optional[str]]:
    """(data_stale, data_as_of) — whether the latest drawdown row is too old."""
    if not latest_row or not latest_row.get("date"):
        return True, None
    as_of = latest_row["date"]
    try:
        row_date = date_cls.fromisoformat(as_of)
        return (date_cls.today() - row_date).days > staleness_days, as_of
    except ValueError:
        return True, as_of
