"""universe.py — NS-7 eligibility check + two-league state machine (§3 of DESIGN.md).

Pure functions over a snapshot of per-ticker facts. No I/O here (store.py
persists tenure; the server fetches facts). The league logic is the heart of the
service: eligibility is NOT binary on any single day — a stock can transiently
dip below a threshold without being a genuine exit. The 90-day grace period
distinguishes noise from signal.

Key property: demotion/promotion PAUSES assessment but NEVER deletes data.
Momentum history is computed over the continuous series regardless of league.
"""
from __future__ import annotations

from typing import Dict, Optional

import config


# ── Eligibility predicates (§3.1) ───────────────────────────────────────
def is_large_and_indexed(market_cap: float, in_sp500: bool) -> bool:
    """U1 OR U2: in SP500, or market cap > $50B."""
    return bool(in_sp500) or market_cap > config.MARKET_CAP_MIN


def is_liquid(avg_daily_volume: float) -> bool:
    """U3: 20-day average daily volume > 100K shares."""
    return avg_daily_volume > config.MIN_AVG_DAILY_VOLUME


def is_quality(eps_ttm: Optional[float], cfo_ttm: Optional[float]) -> bool:
    """U4: positive trailing-12m EPS AND positive trailing-12m operating cash flow.

    None means "not available" → fails the floor (conservative: a missing
    fundamental is treated as not-yet-proven, which lands it in Minor, not Major).
    """
    if eps_ttm is None or cfo_ttm is None:
        return False
    ok = True
    if config.REQUIRE_POSITIVE_EPS:
        ok = ok and eps_ttm > 0
    if config.REQUIRE_POSITIVE_CFO:
        ok = ok and cfo_ttm > 0
    return ok


def meets_all_criteria(facts: Dict) -> bool:
    """All four eligibility gates (U1/U2 OR + U3 AND + U4 AND)."""
    return (
        is_large_and_indexed(facts.get("market_cap", 0.0), facts.get("in_sp500", False))
        and is_liquid(facts.get("avg_daily_volume", 0.0))
        and is_quality(facts.get("eps_ttm"), facts.get("cfo_ttm"))
    )


# ── League transition (§3.2) ────────────────────────────────────────────
def transition(current_league: str, compliant_now: bool,
               consecutive_compliant: int, consecutive_noncompliant: int,
               first_seen_elder_than_grace: bool) -> str:
    """Decide the next league for one ticker.

    Args:
        current_league: 'major', 'minor', or 'removed'.
        compliant_now: does it pass all four criteria today?
        consecutive_compliant: running days passing criteria (while Minor).
        consecutive_noncompliant: running days failing criteria.
        first_seen_elder_than_grace: has the ticker been tracked > grace period?
            (Used to expire fresh Minor entries that never graduate.)

    Returns: the next league.

    Rules (DESIGN.md §3.2):
      1. Minor + compliant for GRACE_PERIOD_DAYS consecutive days → Major.
      2. Major + any failure today → Minor (immediate demotion).
      3. Fresh entry (newly eligible) starts Minor and must graduate.
      4. Minor + noncompliant for GRACE_PERIOD_DAYS consecutive days → removed.
    """
    grace = config.GRACE_PERIOD_DAYS

    if current_league == config.LEAGUE_MAJOR:
        return config.LEAGUE_MAJOR if compliant_now else config.LEAGUE_MINOR

    if current_league == config.LEAGUE_REMOVED:
        # A removed ticker may re-enter only as a fresh Minor (re-IPO, re-listing).
        return config.LEAGUE_REMOVED

    # current_league == MINOR
    if compliant_now and consecutive_compliant >= grace:
        return config.LEAGUE_MAJOR
    if not compliant_now and consecutive_noncompliant >= grace:
        return config.LEAGUE_REMOVED
    return config.LEAGUE_MINOR


def advance_tenure(current_league: str, compliant_now: bool,
                   consecutive_compliant: int,
                   consecutive_noncompliant: int) -> tuple[int, int]:
    """Update the consecutive-compliant/noncompliant counters.

    Returns (new_compliant, new_noncompliant). Compliant streak resets on a
    failure and vice-versa; the OTHER counter is not reset to zero on promotion/
    demotion — it continues accumulating so the grace clock is monotonic.
    """
    if compliant_now:
        return consecutive_compliant + 1, 0
    return 0, consecutive_noncompliant + 1


def is_assessable(league: str) -> bool:
    """Only Major-league tickers are eligible for momentum ranking."""
    return league == config.LEAGUE_MAJOR
