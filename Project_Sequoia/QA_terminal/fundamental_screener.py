"""
fundamental_screener.py — Fundamental Screens page (Phase 2.2).

Cross-references the four validated frameworks (Graham / Greenblatt / Lynch /
Buffett) per ticker from the point-in-time store (fundamentals_history.py),
using the EXACT scorers the walk-forward study validated
(validate_frameworks.py) — single source of truth: what the screen shows IS
what was validated OOS.

  - as_of = today; snapshot = latest annual facts filed <= as_of; price =
    latest close. No live fundamental fetch on page load (store freshness =
    running fundamentals_history.py).
  - agreement = # of the 4 methods passing (0-4). The study's sweet spot was
    >=2 (Sharpe 0.82, +3.97pp vs base) — the UI defaults to that filter.
  - Display metrics (PEG, ROE, EY/ROC...) are supplementary detail; the PASS
    verdicts come from the imported study scorers.
"""
import time
from datetime import datetime, timedelta

import fundamentals_history as fh
from validate_frameworks import (score_graham, score_greenblatt,
                                 score_lynch, score_buffett)

CACHE_TTL = 600
_cache = {}


def screen_universe(as_of=None, force=False):
    """Rows [{ticker, price, snapshot_*, agreement, graham, greenblatt,
    lynch, buffett, fwd_1y}], sorted by agreement desc. Cached CACHE_TTL."""
    as_of = as_of or datetime.now().strftime("%Y-%m-%d")
    now = time.time()
    hit = _cache.get(as_of)
    if hit and not force and now - hit[1] < CACHE_TTL:
        return hit[0]

    snapshots = {}
    for t in sorted({r[0] for r in fh._conn().execute(
            "SELECT DISTINCT ticker FROM annual")}):
        snap = fh.get_snapshot(t, as_of)
        price = fh.price_on(t, as_of)
        if snap and price:
            snapshots[t] = (snap, price)

    peers = _greenblatt_peers(snapshots)   # cross-sectional EY/ROC ranks
    rows = []
    for t, (snap, price) in snapshots.items():
        g_pass = score_graham(snap, price)
        gb_pass = score_greenblatt(snap, price, peers)
        ly_pass = score_lynch(t, snap, price, as_of)
        bf_pass = score_buffett(snap)
        row = {
            "ticker": t,
            "price": price,
            "snapshot_period": snap["period_end"],
            "snapshot_filed": snap["filed"],
            "agreement": sum([g_pass, gb_pass, ly_pass, bf_pass]),
            "graham": {"pass": g_pass, **_graham_detail(snap, price)},
            "greenblatt": {"pass": gb_pass, **_greenblatt_detail(snap, price)},
            "lynch": {"pass": ly_pass, **_lynch_detail(t, snap, price, as_of)},
            "buffett": {"pass": bf_pass, **_buffett_detail(snap)},
            "fwd_1y": _trailing_1y(t, as_of),
        }
        rows.append(row)
    rows.sort(key=lambda r: r["agreement"], reverse=True)
    _cache[as_of] = (rows, now)
    return rows


# --------------------------------------------------------------------------- #
# Greenblatt cross-section (mirrors validate_frameworks.run_study peers)
# --------------------------------------------------------------------------- #
def _greenblatt_peers(snapshots):
    peers = {"ey": [], "roc": []}
    for snap, price in snapshots.values():
        oi = snap["operating_income"]
        if not oi or oi <= 0:
            continue
        shares = snap["shares_outstanding"]
        mcap = price * shares if (shares and price) else None
        ev = (mcap + (snap["short_term_debt"] or 0) + (snap["long_term_debt"] or 0)
              - (snap["cash"] or 0) - (snap["marketable_securities"] or 0)) if mcap else None
        if mcap and ev and ev > 0:
            peers["ey"].append(oi / ev)
        nwc = (snap["current_assets"] or 0) - (snap["current_liabilities"] or 0)
        ic = nwc + (snap["ppe"] or 0)
        if ic and ic > 0:
            peers["roc"].append(oi / ic)
    return peers


# --------------------------------------------------------------------------- #
# Display-only detail (verdicts come from the study scorers)
# --------------------------------------------------------------------------- #
def _graham_detail(snap, price):
    import fundamentals as f
    inc = [{"period": snap["period_end"], "type": "FY",
            "revenue": snap["revenue"], "gross_profit": snap["gross_profit"],
            "net_income": snap["net_income"], "eps_diluted": snap["eps_diluted"]}]
    bs = [{"period": snap["period_end"], "type": "FY",
           "current_assets": snap["current_assets"],
           "current_liabilities": snap["current_liabilities"],
           "short_term_debt": snap["short_term_debt"],
           "long_term_debt": snap["long_term_debt"],
           "total_equity": snap["total_equity"],
           "shares_outstanding": snap["shares_outstanding"],
           "cash": snap["cash"], "net_receivables": None,
           "total_liabilities": snap["total_liabilities"]}]
    m = f.calculate_graham_metrics(inc, bs, [], {
        "price": price, "shares_outstanding": snap["shares_outstanding"]}, None)
    return {"score": m.get("valuation_score"), "rating": m.get("rating"),
            "pe": m.get("pe_ratio"), "graham_number": m.get("graham_number")}


def _greenblatt_detail(snap, price):
    oi = snap["operating_income"]
    shares = snap["shares_outstanding"]
    mcap = price * shares if (shares and price) else None
    ev = (mcap + (snap["short_term_debt"] or 0) + (snap["long_term_debt"] or 0)
          - (snap["cash"] or 0) - (snap["marketable_securities"] or 0)) if mcap else None
    nwc = (snap["current_assets"] or 0) - (snap["current_liabilities"] or 0)
    ic = nwc + (snap["ppe"] or 0)
    return {"ey": round(oi / ev, 4) if (oi and ev) else None,
            "roc": round(oi / ic, 4) if (oi and ic) else None}


def _lynch_detail(ticker, snap, price, as_of):
    eps = snap["eps_diluted"]
    hist = [h for h in fh.history(ticker)
            if h["period_end"] < snap["period_end"] and h["filed"] <= as_of
            and h["eps_diluted"]]
    growth = None
    if eps and eps > 0 and len(hist) >= 5 and hist[-5]["eps_diluted"] and hist[-5]["eps_diluted"] > 0:
        growth = (eps / hist[-5]["eps_diluted"]) ** (1 / 5) - 1
    pe = (price / eps) if (eps and eps > 0 and price) else None
    peg = (pe / (growth * 100)) if (pe and growth and growth > 0) else None
    return {"pe": round(pe, 2) if pe else None,
            "growth": round(growth, 4) if growth is not None else None,
            "peg": round(peg, 2) if peg is not None else None}


def _buffett_detail(snap):
    ni, eq = snap["net_income"], snap["total_equity"]
    roe = (ni / eq) if (ni and eq) else None
    fcf = (snap["operating_cf"] or 0) + (snap["capex"] or 0)
    fcf_conv = (fcf / ni) if (ni and fcf is not None) else None
    debt = (snap["short_term_debt"] or 0) + (snap["long_term_debt"] or 0)
    return {"roe": round(roe, 4) if roe is not None else None,
            "fcf_conv": round(fcf_conv, 4) if fcf_conv is not None else None,
            "de": round(debt / eq, 4) if eq else None}


def _trailing_1y(ticker, as_of):
    try:
        prev = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
        p0, p1 = fh.price_on(ticker, prev), fh.price_on(ticker, as_of)
        return round(p1 / p0 - 1, 4) if (p0 and p1) else None
    except ValueError:
        return None


ROUTES = {'/api/fundamentals/screen': 'handle_fundamentals_screen'}
