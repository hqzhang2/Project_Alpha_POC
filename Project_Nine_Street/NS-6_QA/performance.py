"""
performance.py — NS-6 live performance scoreboard (G2).

Pure metrics over the persisted `performance_log` rows (written daily by the
price feed): trailing total/annualized return, vol, Sharpe, max drawdown,
benchmark excess, per-ticker attribution, and backtest-vs-live reconciliation.

House rules:
- PURE — no I/O except `load_backtest_baseline` (a file read, fail-open).
  Everything else operates on lists/dicts passed in, so tests are hermetic.
- All returns are FRACTIONS (0.05 = 5%); reconciliation reports `delta_pp`
  in percentage points (5.0 = 5pp) per the G2 spec.
- Max drawdown uses the same running-peak math as `budget.compute_drawdown`
  (money-path formula — reused, never changed).
- Fail-open: empty/short input -> None, never a crash or a fake number.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("ns6.performance")

TRADING_DAYS = 252
BASELINE_PATH = Path(__file__).resolve().parent / "data" / "ns6_backtest_baseline.json"


# ── Pure metrics ──────────────────────────────────────────────────────────
def _rets_from_navs(navs: List[float]) -> List[float]:
    """Daily returns from a chronological NAV series (nav_t/nav_{t-1} - 1)."""
    return [navs[i] / navs[i - 1] - 1.0 for i in range(1, len(navs))]


def _max_drawdown(navs: List[float]) -> float:
    """Worst running-peak drawdown over the series (negative fraction)."""
    peak = navs[0]
    dd = 0.0
    for v in navs:
        if v > peak:
            peak = v
        if peak > 0:
            dd = min(dd, v / peak - 1.0)
    return dd


def trailing_metrics(navs: List[float], window: Optional[int] = None) -> Optional[Dict]:
    """Trailing metrics for a chronological NAV series.

    window: trailing N trading days, or None for since-inception.
    Returns None when there are fewer than 2 NAV points (fail-open).

    total_return   = nav_t / nav_0 - 1
    annualized     = (nav_t/nav_0)^(252/n) - 1
    vol            = std(daily_ret) * sqrt(252)
    sharpe         = mean(daily_ret)/std(daily_ret) * sqrt(252)  (rf = 0)
    max_drawdown   = worst running-peak drawdown (budget.compute_drawdown math)
    """
    if not navs or len(navs) < 2:
        return None
    # window = trailing N TRADING DAYS (returns) -> N+1 NAV points.
    if window and len(navs) > window + 1:
        seg = navs[-(window + 1):]
    else:
        seg = navs
    n = len(seg) - 1
    if n < 1 or seg[0] <= 0:
        return None
    rets = _rets_from_navs(seg)
    total_return = seg[-1] / seg[0] - 1.0
    ann = (seg[-1] / seg[0]) ** (TRADING_DAYS / n) - 1.0
    vol = None
    sharpe = None
    if len(rets) >= 2:
        mean_r = sum(rets) / len(rets)
        var_r = sum((r - mean_r) ** 2 for r in rets) / (len(rets) - 1)
        std_r = var_r ** 0.5
        vol = std_r * (TRADING_DAYS ** 0.5)
        sharpe = (mean_r / std_r) * (TRADING_DAYS ** 0.5) if std_r > 0 else None
    return {
        "n_days": n,
        "total_return": round(total_return, 4),
        "annualized_return": round(ann, 4),
        "vol": round(vol, 4) if vol is not None else None,
        "sharpe": round(sharpe, 4) if sharpe is not None else None,
        "max_drawdown": round(_max_drawdown(seg), 4),
    }


def compounded_return(daily_rets: List[Optional[float]]) -> Optional[float]:
    """Compounded total return from daily return fractions (None-tolerant)."""
    vals = [r for r in daily_rets if r is not None]
    if not vals:
        return None
    prod = 1.0
    for r in vals:
        prod *= (1.0 + r)
    return prod - 1.0


def attribution(rows: List[Dict], window: Optional[int] = None) -> Dict:
    """Per-ticker contribution summed over the window (rows chronological).

    Each row's `contributions` = {ticker: w_i * r_i} for that day (persisted
    by the feed). Sum = portfolio return over the window (within rounding).
    """
    seg = rows[-window:] if window and len(rows) > window else rows
    agg: Dict[str, float] = {}
    for r in seg:
        for tk, c in (r.get("contributions") or {}).items():
            agg[tk] = agg.get(tk, 0.0) + float(c)
    ordered = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)
    return {
        "top": [{"ticker": t, "contribution": round(c, 6)} for t, c in ordered[:5]],
        "bottom": [{"ticker": t, "contribution": round(c, 6)}
                   for t, c in reversed(ordered[-5:]) if c < 0],
        "sum_contributions": round(sum(agg.values()), 6),
        "n_tickers": len(agg),
    }


def reconcile(live_map: Dict[str, float], bt_map: Dict[str, float],
              divergence_pp: float = 5.0) -> Optional[Dict]:
    """Overlay live vs walk-forward backtest NAV on the SAME dates.

    live_map / bt_map: {date: nav}. Only common dates are compared.
    delta_pp = (live_total_ret - backtest_total_ret) * 100. Divergent when
    |delta_pp| > divergence_pp (theta `performance.reconcile_divergence_pp`).
    Returns None when fewer than 2 common dates (fail-open).
    """
    common = sorted(set(live_map) & set(bt_map))
    if len(common) < 2:
        return None
    lv = [live_map[d] for d in common]
    bv = [bt_map[d] for d in common]
    if lv[0] <= 0 or bv[0] <= 0:
        return None
    live_ret = lv[-1] / lv[0] - 1.0
    bt_ret = bv[-1] / bv[0] - 1.0
    delta_pp = (live_ret - bt_ret) * 100.0
    return {
        "live_total_ret": round(live_ret, 4),
        "backtest_total_ret": round(bt_ret, 4),
        "delta_pp": round(delta_pp, 2),
        "divergent": abs(delta_pp) > divergence_pp,
        "overlap_days": len(common),
    }


# ── Baseline (fail-open file read; harness writes it) ────────────────────
def load_backtest_baseline(path: Optional[Path] = None) -> Optional[Dict[str, float]]:
    """{date: nav} from the precomputed baseline JSON, or None (fail-open)."""
    path = path or BASELINE_PATH
    try:
        if path.exists():
            with open(path) as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return {str(k): float(v) for k, v in data.items() if v is not None}
    except Exception as exc:  # noqa: BLE001
        log.warning("load backtest baseline failed: %s", exc)
    return None


def refresh_baseline(closes, start, end, top_n: int = 12,
                     out_path: Optional[Path] = None) -> Optional[str]:
    """Run ns6_backtest.simulate and persist {date: nav} (reconciliation input).

    The harness for the G2 reconciliation — call from a script/cron, NOT from
    a GET (a full walk-forward run is heavy). Returns the written path or
    None if the backtest produced no series.
    """
    try:
        import ns6_backtest
    except Exception as exc:  # noqa: BLE001
        log.warning("refresh_baseline: ns6_backtest import failed: %s", exc)
        return None
    res = ns6_backtest.simulate(closes, start, end, top_n=top_n)
    nav = (1 + res["daily_port_ret"]).cumprod()
    if nav is None or len(nav) == 0:
        return None
    mapping = {d.strftime("%Y-%m-%d"): float(v) for d, v in nav.items()}
    out_path = out_path or BASELINE_PATH
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as fh:
            json.dump(mapping, fh, indent=1)
        return str(out_path)
    except Exception as exc:  # noqa: BLE001
        log.warning("refresh_baseline: write failed: %s", exc)
        return None
