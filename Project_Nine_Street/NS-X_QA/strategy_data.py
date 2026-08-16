"""strategy_data.py — NS-DS: per-strategy return-stream store.

Centralizes each strategy's LIVE-ISH realized return stream so NS-X's rotation
can run on REAL, DIFFERENTIATED strategy P&L instead of the SPY proxy that made
every strategy identical (and NS-X a no-op).

Honest sourcing (design §4.5 — return_stream is the strategy's realized P&L,
NOT backtest P&L). Current reality: the paper book was only just created by
NS-PC, so no true "live" NAV exists yet for the equity sleeves. We seed the
store from the best REAL per-strategy data available and LABEL the source:

  strategy   source                        granularity    honesty label
  ns8        NS-8 6-ETF daily closes       daily (real)   'live' (book exists)
  ns7        NS-7 momentum walkforward     monthly→daily  'walkforward'
  at_val     NS-7 value blend              yearly→daily   'walkforward' (thinnest)
  cash       flat 0                        daily          'reference'

Fail-open: any strategy whose source is unavailable/short returns [] → NS-X
quality-floors it to weight 0 (survivors absorb). The store is deterministic:
same sources → same streams. No look-ahead (a period's return is applied at that
period's end).

This is a pragmatic seed, NOT the v4 centralized DB. It reads existing data
files (decoupled, house pattern) and caches the computed daily streams to
data/strategy_streams.json. The v4 store replaces the file reads with a real DB.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import config

log = logging.getLogger("nsds")

STREAMS_PATH = Path(__file__).resolve().parent / "data" / "strategy_streams.json"


# ── Real-data sources (decoupled reads) ──────────────────────────────────
def _load_ns8_closes() -> Dict[str, List[float]]:
    """Real 6-ETF daily closes from NS-8's cache."""
    try:
        doc = json.loads(config.NS8_HIST.read_text())
        return {t: [c for c in doc["closes"][t] if c is not None] for t in doc["tickers"]}
    except Exception:
        return {}


def _load_ns7_momentum_monthly() -> List[dict]:
    """Real momentum walkforward monthly returns (2016–2026)."""
    try:
        doc = json.loads(config.NS7_WF.read_text())
        return doc.get("monthly", [])
    except Exception:
        return []


def _load_ns7_value_yearly() -> List[dict]:
    """Real value blend yearly returns (2016–2018 — thinnest source)."""
    try:
        doc = json.loads(config.NS7_BLEND.read_text())
        return doc.get("value", {}).get("yearly", [])
    except Exception:
        return []


# ── Daily stream builders ────────────────────────────────────────────────
def _daily_returns_from_closes(closes: Dict[str, List[float]],
                               weights: Optional[Dict[str, float]] = None) -> List[float]:
    """Equal-weight (or given-weight) daily book returns from aligned closes."""
    if not closes:
        return []
    tickers = list(closes.keys())
    n = min(len(v) for v in closes.values())
    if n < 2:
        return []
    w = weights or {t: 1.0 / len(tickers) for t in tickers}
    rets = []
    for i in range(1, n):
        day_ret = 0.0
        for t in tickers:
            p0, p1 = closes[t][i - 1], closes[t][i]
            if p0:
                day_ret += w.get(t, 0.0) * (p1 / p0 - 1.0)
        rets.append(day_ret)
    return rets


def _monthly_to_daily(monthly_returns: List[dict], days_per_month: int = 21) -> List[float]:
    """Expand a monthly-return series to a daily step series (no look-ahead:
    each month's return is applied flat across that month's trading days)."""
    daily = []
    for m in monthly_returns:
        r = float(m.get("strategy", 0.0))
        daily.extend([r / days_per_month] * days_per_month)
    return daily


def _yearly_to_daily(yearly_returns: List[dict], days_per_year: int = 252) -> List[float]:
    """Expand a yearly-return series to a daily step series."""
    daily = []
    for y in yearly_returns:
        r = float(y.get("strategy", 0.0))
        daily.extend([r / days_per_year] * days_per_year)
    return daily


# ── Build all streams ─────────────────────────────────────────────────────
def build_streams() -> Dict[str, Dict]:
    """Return {strategy_id: {'returns': [...], 'source': label, 'label': honesty}}."""
    streams: Dict[str, Dict] = {}

    # NS-8: real daily 6-ETF book (equal-weight risky; the live book exists)
    closes = _load_ns8_closes()
    risky = [t for t in closes if t not in ("SHV",)]
    w8 = {t: 1.0 / len(risky) for t in risky} if risky else None
    ns8_ret = _daily_returns_from_closes(closes, w8)
    streams["ns8"] = {"returns": ns8_ret, "source": "ns8_hist_closes",
                      "label": "live" if ns8_ret else "unavailable"}

    # NS-7 momentum: real momentum walkforward monthly → daily
    mom = _monthly_to_daily(_load_ns7_momentum_monthly())
    streams["ns7"] = {"returns": mom, "source": "ns7_walkforward_monthly",
                      "label": "walkforward" if mom else "unavailable"}

    # A_T value: real value blend yearly → daily (thinnest; fail-open if short)
    val = _yearly_to_daily(_load_ns7_value_yearly())
    streams["at_val"] = {"returns": val, "source": "ns7_value_blend_yearly",
                         "label": "walkforward" if len(val) > config.MOM_LOOKBACK_DAYS
                         else "too_short"}

    # cash: flat 0 reference
    n = max((len(s["returns"]) for s in streams.values()), default=0)
    streams["cash"] = {"returns": [0.0] * max(n, 300), "source": "reference",
                       "label": "reference"}
    return streams


def load_streams() -> Dict[str, Dict]:
    """Load cached streams, or build+persist if missing/stale."""
    if STREAMS_PATH.exists():
        try:
            cached = json.loads(STREAMS_PATH.read_text())
            if cached.get("built_at") and \
               (datetime.now() - datetime.fromisoformat(cached["built_at"])).days < 1:
                return cached["streams"]
        except Exception:
            pass
    streams = build_streams()
    STREAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STREAMS_PATH.write_text(json.dumps(
        {"built_at": datetime.now().isoformat(), "streams": streams}, indent=2))
    return streams


def get_stream(strategy_id: str) -> List[float]:
    """The daily return stream for a strategy (fail-open: [] on missing)."""
    streams = load_streams()
    return streams.get(strategy_id, {}).get("returns", [])


def sources() -> Dict[str, str]:
    """Honesty labels per strategy (for the alloc doc + dashboard)."""
    streams = load_streams()
    return {sid: s.get("label", "unavailable") for sid, s in streams.items()}


if __name__ == "__main__":
    s = load_streams()
    for k, v in s.items():
        print(f"{k}: {len(v['returns'])} pts, {v['label']} ({v['source']})")
