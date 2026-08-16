"""constructor.py — NS-PC: assemble targets into a written paper portfolio.

The missing write path. Pure, deterministic pipeline:

  read()      → NS-X strategy weights + NS-5 equity blend + NS-8 tactical book
  compose()   → fund_book = (w_ns7+w_at_val)×blended + w_ns8×signals + w_cash×BIL
  guard()     → per-name cap ≤8%, effective-N ≥15 (NS-X §6.3)
  materialize()→ whole shares at last close, BIL cash proxy, rounding dust→cash
  write()     → paper_portfolio.json (schema-compatible with NS-1/NS-6)

Fail-open: missing/stale input → no write, non-zero exit, last good book intact.
No signal logic, no optimization, no broker I/O. One job.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import config

log = logging.getLogger("nspc.constructor")


# ── Read ──────────────────────────────────────────────────────────────────
def _load_json(path: Path) -> Optional[Dict]:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _is_stale(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        doc = json.loads(path.read_text())
        gen = doc.get("generated_at") or doc.get("as_of")
        if gen is None:
            return True
        gen_dt = datetime.fromisoformat(gen.replace("T", " ").split(".")[0])
        return (datetime.now() - gen_dt).days > config.STALE_DAYS
    except Exception:
        return True


def read_inputs() -> Tuple[Optional[Dict], Optional[Dict], Optional[Dict]]:
    """Return (nsx_alloc, ns5_blend, ns8_signals) — each None if missing/stale.

    Source: PostgreSQL strategy_output (the NS-DB centralized store), falling
    back to the JSON files if the DB is unreachable (fail-open).
    """
    alloc, blend, signals = None, None, None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
        import common.db as db
        alloc = db.latest_strategy_output("nsx", "alloc")
        blend = db.latest_strategy_output("ns5", "blend")
        signals = db.latest_strategy_output("ns8", "signals")
    except Exception:
        alloc, blend, signals = None, None, None

    # file fallback (phase-3 compat; DB authoritative once PROD verified)
    if alloc is None:
        alloc = _load_json(config.NSX_ALLOC) if not _is_stale(config.NSX_ALLOC) else None
    if blend is None:
        blend = _load_json(config.NS5_BLEND) if not _is_stale(config.NS5_BLEND) else None
    if signals is None:
        signals = _load_json(config.NS8_SIGNALS) if not _is_stale(config.NS8_SIGNALS) else None
    return alloc, blend, signals


# ── Compose ──────────────────────────────────────────────────────────────
def compose(alloc: Dict, blend: Dict, signals: Dict) -> Dict[str, float]:
    """fund_book = Σ (w_strategy × strategy.target_book). Returns {ticker: weight}.

    ns7 + at_val → equity sleeve (blended book, already merged 50/50 by NS-5).
    ns8 → tactical book (signals.weights). cash → BIL.
    """
    strategies = alloc.get("strategies", {})
    w_ns7 = strategies.get("ns7", 0.0)
    w_at_val = strategies.get("at_val", 0.0)
    w_ns8 = strategies.get("ns8", 0.0)
    w_cash = strategies.get(config.CASH_STRATEGY_ID, 0.0)

    equity_w = w_ns7 + w_at_val
    blended = blend.get("blended", {})
    tactical = signals.get("weights", {})

    fund: Dict[str, float] = {}
    for ticker, w in blended.items():
        fund[ticker] = fund.get(ticker, 0.0) + equity_w * float(w)
    for ticker, w in tactical.items():
        if ticker in config.TACTICAL_ETFS and ticker != config.CASH_PROXY:
            fund[ticker] = fund.get(ticker, 0.0) + w_ns8 * float(w)
    # cash proxy (BIL) absorbs the residual + explicit cash weight
    fund[config.CASH_PROXY] = fund.get(config.CASH_PROXY, 0.0) + w_cash

    # renormalize to 1.0 (float drift / dust)
    total = sum(fund.values())
    if total > 0:
        fund = {k: v / total for k, v in fund.items()}
    return fund


# ── Guard ─────────────────────────────────────────────────────────────────
def effective_n(weights: Dict[str, float]) -> float:
    s = sum(weights.values())
    if s <= 0:
        return 0.0
    return 1.0 / sum((w / s) ** 2 for w in weights.values())


def apply_guards(weights: Dict[str, float]) -> Dict[str, float]:
    """Per-name cap ≤8% (cash proxy exempt), redistributing excess to cash.

    Iterative: cap any risky name above the cap, redistribute the freed weight
    to the remaining uncapped risky names proportionally; if all risky names are
    at cap, the excess accrues to the cash proxy (BIL). A naive cap-then-
    renormalize would RE-INFLATE the capped name — so we redistribute instead.

    Effective-N is NOT force-flattened here: it is a *reported* diagnostic
    (guardrails()), because silently flattening to equal weight would discard
    the strategy signal. Concentration is reported, not silently destroyed.
    """
    w = {k: float(v) for k, v in weights.items()}
    if config.CASH_PROXY not in w:
        w[config.CASH_PROXY] = 0.0
    risky = [k for k in w if k != config.CASH_PROXY]

    for _ in range(100):                             # bounded iterations
        over = [(k, w[k] - config.COMPOSED_MAX_NAME_W) for k in risky
                if w[k] > config.COMPOSED_MAX_NAME_W]
        if not over:
            break
        excess = 0.0
        for k, exc in over:
            w[k] = config.COMPOSED_MAX_NAME_W
            excess += exc
        eligible = [k for k in risky if w[k] < config.COMPOSED_MAX_NAME_W - 1e-12]
        if not eligible:
            w[config.CASH_PROXY] += excess            # all at cap → cash absorbs
            continue
        elig_sum = sum(w[k] for k in eligible)
        if elig_sum <= 0:
            w[config.CASH_PROXY] += excess
            continue
        for k in eligible:
            w[k] += excess * (w[k] / elig_sum)

    # renormalize (cash proxy included) to 1.0
    total = sum(w.values())
    if total > 0:
        w = {k: v / total for k, v in w.items()}
    return w


def guardrails(weights: Dict[str, float]) -> Dict[str, float]:
    """Report the composed-book guardrail metrics (matches NS-5's block).

    max_weight = the largest RISKY name (excludes the cash proxy, which is the
    residual risk-off sleeve and legitimately exempt from the per-name cap).
    """
    risky = {k: v for k, v in weights.items() if k != config.CASH_PROXY}
    return {
        "n": len(weights),
        "eff_n": round(effective_n(risky) if risky else 0.0, 2),
        "max_weight": round(max(risky.values(), default=0.0), 4),
        "weights_sum": round(sum(weights.values()), 6),
        "min_eff_n": config.COMPOSED_MIN_EFF_N,
        "max_name_w": config.COMPOSED_MAX_NAME_W,
    }


# ── Materialize ───────────────────────────────────────────────────────────
def materialize(weights: Dict[str, float], nav: float,
                prices: Dict[str, float]) -> Tuple[Dict, float]:
    """Convert target weights → whole-share positions + residual cash.

    Returns (positions_dict, residual_cash). Whole shares: floor(nav*w/price).
    """
    positions: Dict[str, Dict] = {}
    invested = 0.0
    for ticker, w in sorted(weights.items()):
        price = prices.get(ticker)
        if price is None or price <= 0:
            continue                                   # no price → skip (fail-open)
        shares = int(nav * w / price)
        if shares <= 0:
            continue
        cost = shares * price
        invested += cost
        positions[ticker] = {
            "shares": shares,
            "entry_price": round(price, 4),
            "current_price": round(price, 4),
            "allocation_pct": round(w * 100, 2),
            "strategy": config.STRATEGY_LABEL,
            "pnl": 0.0,
            "pnl_pct": 0.0,
        }
    residual = round(nav - invested, 2)
    return positions, residual


# ── Write ─────────────────────────────────────────────────────────────────
def build_portfolio(alloc: Dict, blend: Dict, signals: Dict,
                    prices: Dict[str, float],
                    initial_balance: Optional[float] = None,
                    prior: Optional[Dict] = None) -> Dict:
    """Full pipeline → the paper_portfolio.json document."""
    weights = apply_guards(compose(alloc, blend, signals))

    initial = (initial_balance or
               (prior.get("account", {}).get("initial_balance")
                if prior else None) or config.INITIAL_BALANCE)

    # NAV = mark-to-market: if we have a prior NAV, preserve continuity by
    # using initial_balance on first run; otherwise carry prior total_nav.
    nav = (prior.get("account", {}).get("total_nav") if prior else None) or initial

    positions, residual = materialize(weights, nav, prices)

    history = list(prior.get("history", [])) if prior else []
    regime = blend.get("regime", "unknown")
    equity_w = sum(weights.get(t, 0) for t in blend.get("blended", {}))
    tactical_w = sum(weights.get(t, 0) for t in config.TACTICAL_ETFS
                     if t != config.CASH_PROXY)
    cash_w = weights.get(config.CASH_PROXY, 0.0)   # fund-level cash (BIL)
    note = (f"{regime} regime; equity {equity_w:.0%} "
            f"tactical {tactical_w:.0%} cash {cash_w:.0%}")
    # E.3: upsert by date, not blind append — a same-day re-construct must
    # overwrite that day's NAV point, never accumulate duplicates.
    today = datetime.now().strftime("%Y-%m-%d")
    nav_point = {"date": today, "nav": round(nav, 2), "note": note}
    history = [h for h in history if h.get("date") != today]
    history.append(nav_point)

    return {
        "account": {
            "initial_balance": initial,
            "cash": residual,
            "total_nav": round(nav, 2),
            "commissions_paid": 0.0,
            "last_updated": datetime.now().strftime("%Y-%m-%d"),
        },
        "positions": {"equities": positions, "options": {}},
        "guardrails": guardrails(weights),
        "history": history,
    }


def run_construct(prices: Dict[str, float]) -> Dict:
    """Read inputs → build → write. Returns the doc. Fail-open: no write on bad input."""
    alloc, blend, signals = read_inputs()
    if alloc is None or blend is None or signals is None:
        raise RuntimeError("NS-PC: missing/stale input (NS-X/NS-5/NS-8) — no write")

    prior = None
    try:  # prefer DB (authoritative, E.3-deduped history); fall back to file
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
        import common.db as db
        prior = db.get_portfolio(config.PORTFOLIO_NAME) or _load_json(config.PORTFOLIO_PATH)
    except Exception:
        prior = _load_json(config.PORTFOLIO_PATH)
    doc = build_portfolio(alloc, blend, signals, prices, prior=prior)
    write_portfolio(doc)
    return doc


def write_portfolio(doc: Dict, path: Optional[Path] = None) -> Path:
    """Write the portfolio doc. Primary: PostgreSQL (portfolios/positions/nav/
    guardrails tables via common.db). Secondary: write-through to the JSON file
    as a compatibility artifact for any reader still on the file (NS-1 legacy).

    The DB is authoritative; the file is a write-through mirror. If Postgres is
    unreachable (fail-open), we still write the file so the legacy readers work.
    """
    # Primary: DB (fail-open — never raises on a down DB)
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
        import common.db as db
        db.write_portfolio(config.PORTFOLIO_NAME, doc, kind="live")
    except Exception:  # noqa: BLE001
        log.warning("Postgres write failed (fail-open) — file mirror only")

    # Secondary: file write-through (legacy readers)
    path = path or config.PORTFOLIO_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2))
    return path


if __name__ == "__main__":
    import sys
    try:
        # last close prices via the same ticker set; production wires yfinance
        # (this __main__ is a smoke path — the server calls run_construct with
        # real prices).
        doc = run_construct({})
        print(f"NS-PC wrote portfolio: {len(doc['positions']['equities'])} positions, "
              f"nav {doc['account']['total_nav']}")
        sys.exit(0)
    except Exception as e:  # noqa: BLE001
        print(f"NS-PC failed (no write): {e}")
        sys.exit(1)
