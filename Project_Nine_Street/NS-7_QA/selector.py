"""selector.py — NS-7 skip-month momentum + quality veto + concentration caps (§4-5).

Operates ONLY on Major-league tickers. Emits a ranked signal, not weights —
weighting is NS-5's frontier job. Pure functions over price data (a dict of
ticker -> list of closes, oldest-first) plus per-ticker facts.

No drawdown logic here (guardrail G6): NS-7 optimizes return; NS-6 owns the tail.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import config


# ── Momentum signal (§4.1) ──────────────────────────────────────────────
def skip_month_momentum(closes: List[float]) -> Optional[float]:
    """126-day lookback, 21-day skip: P[t-21] / P[t-126] - 1.

    Returns None if the series is too short (caller must have a full history —
    guardrail G2: point-in-time, no lookahead, no backfill of missing series).
    """
    if closes is None or len(closes) < config.MOMENTUM_MIN_HISTORY:
        return None
    lookback = config.MOMENTUM_LOOKBACK_DAYS
    skip = config.MOMENTUM_SKIP_DAYS
    p_old = closes[-lookback]
    p_skip = closes[-skip]
    if not p_old or p_old <= 0:
        return None
    return (p_skip / p_old) - 1.0


# ── Quality veto (§5 G3) ─────────────────────────────────────────────────
def passes_quality_veto(eps_ttm: Optional[float],
                        cfo_ttm: Optional[float]) -> bool:
    """The value/quality layer as a veto, NOT a pick. Deliberately looser than
    the value screener's agreement>=2 (that would be the proven-dead 'B' trap)."""
    from universe import is_quality
    return is_quality(eps_ttm, cfo_ttm)


# ── Ranking & selection (§4.2) ───────────────────────────────────────────
def rank_major(prices: Dict[str, List[float]],
               facts: Dict[str, Dict],
               top_n: Optional[int] = None) -> List[Dict]:
    """Rank Major tickers by skip-month momentum, apply quality veto, cap to top-N.

    Args:
        prices: {ticker: [closes oldest-first]} — only Major tickers expected.
        facts: {ticker: {eps_ttm, cfo_ttm, market_cap, in_sp500, ...}}.
        top_n: cap on the returned ranked list. None → rank ALL scored names
            (used by the pipeline to persist every Major score for /api/major);
            pass an int for the NS-5 feed (config.TOP_N).

    Returns a list of dicts, ranked descending, each:
        {ticker, momentum, rank}  (rank is 1-based; None momentum = excluded)
    """
    scored = []
    for ticker, closes in prices.items():
        mom = skip_month_momentum(closes)
        if mom is None:
            continue  # insufficient history — can't rank (G2)
        scored.append((ticker, mom))

    scored.sort(key=lambda x: x[1], reverse=True)

    ranked = []
    for ticker, mom in scored:
        f = facts.get(ticker, {})
        if not passes_quality_veto(f.get("eps_ttm"), f.get("cfo_ttm")):
            continue  # quality veto removes junk, keeps growth leaders (G3)
        ranked.append({"ticker": ticker, "momentum": round(mom, 6),
                       "rank": len(ranked) + 1})
        if top_n is not None and len(ranked) >= top_n:
            break
    return ranked


# ── Concentration guardrail (§5 G4) ──────────────────────────────────────
def effective_n(weights: Dict[str, float]) -> float:
    """Effective number of positions: 1 / sum(w^2)."""
    s = sum(w * w for w in weights.values() if w > 0)
    return 1.0 / s if s > 0 else 0.0


def apply_turnover_band(ranked_all: List[Dict], held: set,
                        top_n: Optional[int] = None,
                        band: Optional[int] = None) -> List[Dict]:
    """Anti-churn selection (G5 baseball guardrail).

    Ranked list (all scored, quality-vetoed, descending) → final top-N picks:

      1. Names ranked within top (top_n + band) are eligible.
      2. A currently-HELD name inside the band is KEPT even if it slipped
         below top_n (don't trim a position on a transient rank wobble).
      3. Remaining slots fill with the highest-ranked newcomers.
      4. Book capped at top_n names.

    Args:
        ranked_all: full ranked output of rank_major(..., top_n=None).
        held: tickers currently in the book (previous selection).
        top_n: book size (default config.TOP_N).
        band: rank cushion (default config.TURNOVER_BAND).

    Returns the final pick list (ranked desc, length <= top_n).
    """
    top_n = config.TOP_N if top_n is None else top_n
    band = config.TURNOVER_BAND if band is None else band
    cutoff = top_n + band
    eligible = [r for r in ranked_all if r["rank"] <= cutoff]
    kept = [r for r in eligible if r["ticker"] in held]
    newcomers = [r for r in eligible if r["ticker"] not in held]
    picks = kept + newcomers
    return picks[:top_n]


def concentration_ok(weights: Dict[str, float],
                     sector_weights: Optional[Dict[str, float]] = None) -> bool:
    """G4: no single name > 8%, effective-N >= 15, no sector > 40%.

    NOTE: NS-7 emits signals, not weights — this is exposed so the walk-forward
    harness can assert the *resulting* book (after NS-5 frontier) respects the
    caps, and so a naive equal-weight over the top-N can be sanity-checked.
    """
    if any(w > config.MAX_POSITION_WEIGHT for w in weights.values()):
        return False
    if effective_n(weights) < config.MIN_EFFECTIVE_N:
        return False
    if sector_weights:
        if any(w > config.MAX_SECTOR_WEIGHT for w in sector_weights.values()):
            return False
    return True
