"""d1_basket.py — NS-7 DeltaOne basket construction (v4.6).

The portfolio-construction layer: turns the ranked momentum selection into a
WEIGHTED basket ({ticker: weight}) — the ONLY NS-7 → NS-5 handoff
(research_d1_basket_v46.md §3). If no basket exists, NS-5 has no D1 sleeve;
the raw ranked list is never a handoff.

Weighting methods (config.D1_WEIGHT_METHOD, PM-switchable):
  momentum_score   w ∝ max(momentum, 0)            (default)
  rank_tilted      inverse-rank linear or geometric
  risk_normalized  w ∝ 1/σ (ex-ante vol from daily closes)
  tenure_aware     momentum_score × tenure-recency term (favors fresh Majors,
                   de-emphasizes long-tooth names) — evidence-gated, see §4b

Guardrails AFTER weighting (baseball): per-name cap 8% with redistribution to
uncapped names (never re-inflate a capped name); effective-N REPORTED.
Sector cap: NOT yet enforced — selection.json carries no sector field
(flagged in research doc §12); reported once sector data lands.

Tenure ("days on list", PM decision 2026-08-23): days since the ticker's last
transition INTO Major league (ns7_league.last_seen while league='major');
counter resets to 0 on demotion to Minor by construction of last_seen.
All current Majors share the go-live date (2026-08-13), so tenure carries no
signal until the league system ages — mechanical correctness ships now.

Fail-open: missing/stale selection → empty basket, never crash.
DPF-owned methodology: weighting/fail-open semantics are frontier-owned.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

import config

log = logging.getLogger("ns7.d1_basket")

WEIGHT_METHODS = ("momentum_score", "rank_tilted", "risk_normalized",
                  "tenure_aware")


# ── Selection read (fail-open) ────────────────────────────────────────────
def load_selection(path: Optional[Path] = None) -> Optional[Dict]:
    """Read selection.json. None if missing/corrupt."""
    p = Path(path or config.SELECTION_PATH)
    try:
        doc = json.loads(p.read_text())
        return doc if isinstance(doc, dict) and doc.get("scores") else None
    except Exception as exc:  # noqa: BLE001
        log.warning("selection unreadable (%s) — no basket", exc)
        return None


def top_candidates(scores: List[dict], n: Optional[int] = None) -> List[dict]:
    """Top-n scored names by ascending rank (n = config.BASKET_TOP_N default).

    Takes whatever is available if the pool is thinner than n (PM decision
    §3b: fixed editable n, thin pool → thin basket, never padded). Fail-open
    on a missing rank: falls back to list order rather than raising.
    """
    n = n if n is not None else config.BASKET_TOP_N
    key = (lambda s: s["rank"]) if all("rank" in s for s in scores) \
        else (lambda s: float("inf"))
    return sorted(scores, key=key)[:n]


# ── Tenure ────────────────────────────────────────────────────────────────
def tenure_days(tickers: List[str]) -> Dict[str, Optional[int]]:
    """Days since each ticker's current Major stint began; None if not Major.

    Reads common.db.get_league() (public centralized-DB seam). The anchor is
    the major_since column — stamped on entering/promoting INTO Major and
    cleared on demotion (PM rule: reset to 0 when a name drops to Minor).
    Falls back to first_seen for legacy rows predating major_since.
    Fail-open → None.
    """
    out: Dict[str, Optional[int]] = {t: None for t in tickers}
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
        import common.db as db
        today = date.today()
        for t in tickers:
            row = db.get_league(t.upper())
            if not row or row.get("league") != "major":
                continue
            anchor = row.get("major_since") or row.get("first_seen")
            if not anchor:
                continue
            s = anchor[:10] if isinstance(anchor, str) else str(anchor)[:10]
            out[t] = max(0, (today - date.fromisoformat(s)).days)
    except Exception as exc:  # noqa: BLE001
        log.warning("tenure lookup failed (%s) — tenure treated as unknown", exc)
    return out


# ── Weighting methods ─────────────────────────────────────────────────────
def _risk_sigma(closes_by_ticker: Dict[str, List[float]],
                tickers: List[str],
                window: int = 60) -> Dict[str, float]:
    """Ex-ante daily sigma (EWMA-free simple std over `window`) per ticker."""
    import math
    out = {}
    for t in tickers:
        closes = closes_by_ticker.get(t) or []
        tail = closes[-(window + 1):]
        rets = [tail[i] / tail[i - 1] - 1.0
                for i in range(1, len(tail)) if tail[i - 1]]
        if len(rets) < 20:
            out[t] = float("nan")   # insufficient → excluded by caller
            continue
        mu = sum(rets) / len(rets)
        out[t] = math.sqrt(sum((r - mu) ** 2 for r in rets) / len(rets))
    return out


def weight_basket(cands: List[dict],
                  method: str,
                  closes_by_ticker: Optional[Dict[str, List[float]]] = None,
                  tenure: Optional[Dict[str, int]] = None) -> Dict[str, float]:
    """Map candidates → raw unnormalized weights by method. Empty dict → {}."""
    tickers = [c["ticker"] for c in cands]
    if not tickers:
        return {}
    if method not in WEIGHT_METHODS:
        raise ValueError(f"unknown weight method '{method}'; "
                         f"expected one of {list(WEIGHT_METHODS)}")

    if method == "momentum_score":
        # negative momentum earns nothing (floor at 0 keeps the book valid);
        # an all-negative pool → fall back to equal weight so the sleeve survives
        raw = {c["ticker"]: max(float(c.get("momentum", 0.0)), 0.0) for c in cands}
        if sum(raw.values()) <= 0:
            raw = {t: 1.0 for t in tickers}
        return raw

    if method == "rank_tilted":
        # linear inverse-rank (rank 1 heaviest); geometric variant via config
        n = len(cands)
        if getattr(config, "D1_RANK_TILT_GEOMETRIC", False):
            return {c["ticker"]: 2.0 ** (-float(c["rank"])) for c in cands}
        return {c["ticker"]: float(n + 1 - c["rank"]) for c in cands}

    if method == "risk_normalized":
        sig = _risk_sigma(closes_by_ticker or {}, tickers)
        # Missing/NaN vol for a name → neutral weight 1.0 (keep the full book,
        # same semantics as momentum_score's fallback — a thin book is not
        # silently shrunk). Only an ALL-missing book falls back to equal weight.
        w = {}
        have_any = False
        for t in tickers:
            s = sig.get(t)
            if s and s == s and s > 0:
                w[t] = 1.0 / s
                have_any = True
            else:
                w[t] = 1.0
        if not have_any:
            log.warning("no usable vol series — risk_normalized falls back to "
                        "equal weight")
            w = {t: 1.0 for t in tickers}
        return w

    if method == "tenure_aware":
        base = weight_basket(cands, "momentum_score")
        ten = tenure or {}
        # recency factor: full weight ≤ FRESH_DAYS, decays linearly to
        # TENURE_MIN_FACTOR at LONG_TOOTH_DAYS, flat beyond (never zero —
        # demotion is the engine's exit, not tenure)
        fresh = getattr(config, "D1_TENURE_FRESH_DAYS", 63)
        long_tooth = getattr(config, "D1_TENURE_LONG_TOOTH_DAYS", 252)
        min_f = getattr(config, "D1_TENURE_MIN_FACTOR", 0.5)
        out = {}
        for t, w in base.items():
            d = ten.get(t)
            if d is None:
                f = 1.0                       # unknown tenure → neutral
            elif d <= fresh:
                f = 1.0
            elif d >= long_tooth:
                f = min_f
            else:
                f = 1.0 - (d - fresh) / (long_tooth - fresh) * (1.0 - min_f)
            out[t] = w * f
        return out

    raise ValueError(f"unknown weight method '{method}'; "
                     f"expected one of {list(WEIGHT_METHODS)}")


# ── Guardrails ────────────────────────────────────────────────────────────
def apply_guardrails(weights: Dict[str, float],
                     max_w: Optional[float] = None) -> Dict[str, float]:
    """Per-name cap with proportional redistribution to uncapped names.

    Iterative cap-and-redistribute (NS-PC apply_guards pattern): naive
    cap-then-renormalize would RE-INFLATE capped names. Renormalizes to 1.0.
    """
    max_w = max_w if max_w is not None else config.D1_MAX_NAME_W
    total = sum(weights.values())
    if total <= 0:
        return {}
    w = {t: v / total for t, v in weights.items()}
    for _ in range(100):
        over = {t: v for t, v in w.items() if v > max_w}
        if not over:
            break
        excess = sum(v - max_w for v in over.values())
        for t in over:
            w[t] = max_w
        eligible = [t for t, v in w.items() if v < max_w - 1e-12]
        elig_sum = sum(w[t] for t in eligible)
        if not eligible or elig_sum <= 0:
            break   # everything at cap — leave as-is (reported, not padded)
        for t in eligible:
            w[t] += excess * (w[t] / elig_sum)
    total = sum(w.values())
    return {t: v / total for t, v in w.items()} if total > 0 else {}


# ── Assembly ──────────────────────────────────────────────────────────────
def effective_n(weights: Dict[str, float]) -> float:
    s = sum(weights.values())
    if s <= 0:
        return 0.0
    return 1.0 / sum((w / s) ** 2 for w in weights.values())


def build_basket(selection: Optional[Dict] = None,
                 method: Optional[str] = None,
                 n: Optional[int] = None,
                 closes_by_ticker: Optional[Dict[str, List[float]]] = None) -> Optional[Dict]:
    """Full pipeline: selection → top-n → weights → guardrails → basket doc.

    Returns the d1_basket.json-shaped doc, or None if no selection available
    (fail-open contract: no basket → NS-5 has no D1 sleeve).
    """
    sel = selection if selection is not None else load_selection()
    if sel is None:
        return None
    method = method or config.D1_WEIGHT_METHOD
    if method not in WEIGHT_METHODS:
        raise ValueError(f"unknown method '{method}'")

    cands = top_candidates(sel["scores"], n)
    if not cands:
        return None

    tenure = None
    if method == "tenure_aware":
        tenure = tenure_days([c["ticker"] for c in cands])

    raw = weight_basket(cands, method, closes_by_ticker, tenure)
    # Drop non-positive weights (momentum floor can yield 0.0) so top_n/eff_n
    # reflect only the actually-held names, not zeros.
    raw = {t: w for t, w in raw.items() if w and w > 0}
    if not raw:
        return None
    weights = apply_guardrails(raw, config.D1_MAX_NAME_W)

    return {
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "service": "NS-7",
        "strategy": "deltaone",
        "method": method,
        "selection_as_of": sel.get("as_of"),
        "top_n": len(weights),
        "guardrails": {"max_weight": config.D1_MAX_NAME_W,
                       "min_eff_n": config.COMPOSED_MIN_EFF_N},
        "weights": {t: round(w, 6) for t, w in sorted(weights.items())},
        "eff_n": round(effective_n(weights), 2),
        "max_weight": round(max(weights.values()), 6),
        "benchmarks": sel.get("benchmarks"),
    }


def main() -> int:
    # risk_normalized needs per-ticker closes — load them up front so the
    # live path computes real inverse-vol weights (not the equal-weight
    # fallback). Fail-open: missing closes → {} → risk_normalized still works
    # as equal-weight rather than crashing.
    closes = _load_basket_closes()
    doc = build_basket(closes_by_ticker=closes)
    if doc is None:
        log.error("no selection available — no basket written")
        return 1
    path = Path(config.D1_BASKET_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2))
    print(f"D1 basket {doc['as_of']}: method={doc['method']} n={doc['top_n']} "
          f"effN={doc['eff_n']} maxW={doc['max_weight']:.1%} → {path}")
    return 0


def _load_basket_closes(cands: Optional[List[dict]] = None) -> Dict[str, List[float]]:
    """{ticker: [closes asc]} for the current top-n selection (risk_normalized).

    Reuses d1_grading's A_T price read. Fail-open → {} on any error.
    """
    if cands is None:
        sel = load_selection()
        if sel is None:
            return {}
        cands = top_candidates(sel["scores"])
    try:
        import d1_grading
        d = d1_grading._load_closes([c["ticker"] for c in cands])
        return {t: sorted(closes.values()) for t, closes in d.items()}
    except Exception as exc:  # noqa: BLE001
        log.warning("basket closes unavailable (%s) — risk_normalized may fall "
                    "back to equal weight", exc)
        return {}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
