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


# ── Eligibility predicates (§3.1 of DESIGN.md, PM-corrected 2026-08-13) ─
# League criteria are SP500-membership ∪ market-cap only:
#   SP500 member            → MAJOR immediately (index membership IS the ticket)
#   non-SP500, cap > $75B   → MAJOR immediately (PM fast-track)
#   non-SP500, cap > $50B   → MINOR (fresh); Major after 90d OR $75B breach
#   cap ≤ $50B              → not tracked / removed (noncompliance clock)
# Liquidity (U3) and quality (U4) are NOT league gates — they apply at
# SELECTION time (the quality veto in selector.rank_major), so the book is
# still protected from junk without delaying index members' eligibility.
def is_large_and_indexed(market_cap: Optional[float], in_sp500: bool) -> bool:
    """U1 OR U2 (legacy 4-gate helper): in SP500, or market cap > $50B.

    None market cap is "not proven" → the numeric gate fails; SP500
    membership still passes (conservative, missing ≠ proven).
    """
    if in_sp500:
        return True
    return market_cap is not None and market_cap > config.MARKET_CAP_MIN


def is_liquid(avg_daily_volume: Optional[float]) -> bool:
    """U3: 20-day average daily volume > 100K shares. None → not proven."""
    return avg_daily_volume is not None and avg_daily_volume > config.MIN_AVG_DAILY_VOLUME


def is_quality(eps_ttm: Optional[float], cfo_ttm: Optional[float]) -> bool:
    """U4: positive trailing-12m EPS AND positive trailing-12m operating cash flow.

    None means "not available" → fails the floor (conservative: a missing
    fundamental is treated as not-yet-proven). Used as the SELECTION-time
    veto (selector.passes_quality_veto), not a league gate.
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
    """Legacy 4-gate eligibility (U1/U2 OR + U3 AND + U4 AND).

    Retained for reference/tests — the LIVE league gates are the PM-corrected
    league_compliant() / major_qualifying() below.
    """
    return (
        is_large_and_indexed(facts.get("market_cap", 0.0), facts.get("in_sp500", False))
        and is_liquid(facts.get("avg_daily_volume", 0.0))
        and is_quality(facts.get("eps_ttm"), facts.get("cfo_ttm"))
    )


# ── League gates (PM-corrected criteria) ────────────────────────────────
def league_compliant(facts: Dict) -> bool:
    """League floor: a name is tracked/kept when SP500 member OR cap > $50B.

    Missing cap is "not proven" → not compliant (Minor/removal clock runs).
    """
    if facts.get("in_sp500"):
        return True
    return (facts.get("market_cap") or 0) > config.MARKET_CAP_MIN


def major_qualifying(facts: Dict) -> bool:
    """Immediate-Major triggers: SP500 membership OR cap > $75B fast-track."""
    if facts.get("in_sp500"):
        return True
    return (facts.get("market_cap") or 0) > config.MARKET_CAP_MAJOR_FASTTRACK


# ── League transition (§3.2, PM-corrected 2026-08-13) ───────────────────
def transition(current_league: str, major_now: bool, compliant_now: bool,
               consecutive_compliant: int, consecutive_noncompliant: int) -> str:
    """Decide the next league for one ticker under the corrected rules.

    Args:
        current_league: 'major', 'minor', or 'removed'.
        major_now: SP500 member OR cap > $75B (immediate-Major triggers).
        compliant_now: SP500 member OR cap > $50B (league floor).
        consecutive_compliant: running compliant days (while Minor).
        consecutive_noncompliant: running non-compliant days.

    Returns: the next league.

    Rules (PM correction, 2026-08-13):
      1. MAJOR stays while major_now OR compliant_now — a non-SP500 Major
         with $50B < cap ≤ $75B is self-sustaining (its 90-day clock was
         earned); it demotes only when cap ≤ $50B. SP500-removal is handled
         at the orchestration layer (fresh recompute, apply_daily).
      2. MAJOR + cap ≤ $50B → MINOR immediately (noncompliance clock starts).
      3. MINOR + major_now (SP500 add or $75B breach) → MAJOR immediately.
      4. MINOR + compliant for GRACE consecutive days → MAJOR (90-day path).
      5. MINOR + noncompliant for GRACE consecutive days → REMOVED.
      6. REMOVED stays removed (re-admission handled at orchestration).
    """
    grace = config.GRACE_PERIOD_DAYS

    if current_league == config.LEAGUE_MAJOR:
        if major_now or compliant_now:
            return config.LEAGUE_MAJOR
        return config.LEAGUE_MINOR

    if current_league == config.LEAGUE_REMOVED:
        return config.LEAGUE_REMOVED

    # current_league == MINOR
    if major_now:
        return config.LEAGUE_MAJOR
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


# ── Orchestration (fresh entry / re-admission) — shared by pipeline + ───
# ── walk-forward harness (ONE source of truth for league logic) ─────────
def apply_daily(state: Dict[str, Dict], facts_by_ticker: Dict[str, Dict],
                as_of: str, sp500_removed: Optional[set] = None) -> tuple[Dict[str, Dict], Dict]:
    """Advance every tracked/eligible ticker's league state for one day.

    Args:
        state: {ticker: {league, consecutive_compliant,
                consecutive_noncompliant, first_seen, last_seen}} — the
                in-memory mirror of the store's league table.
        facts_by_ticker: {ticker: eligibility facts} for the candidate set.
        as_of: ISO date being processed.
        sp500_removed: set of tickers that left the SP500 since the previous
            refresh (index removal → the non-SP500 cap rule kicks in, i.e.
            the name is RE-COMPUTED as a fresh non-SP500 entry: >$75B stays
            Major, $50-75B → fresh Minor clock, ≤$50B → noncompliance clock).

    Returns (new_state, counts). Rules (PM-corrected 2026-08-13):
      - SP500 member → MAJOR immediately (fresh or re-admitted alike).
      - Non-SP500 cap > $75B → MAJOR immediately (fast-track).
      - Non-SP500 $50B < cap ≤ $75B → fresh MINOR; Major after 90 compliant
        days or an immediate $75B breach.
      - cap ≤ $50B → not added; tracked names run the noncompliance clock
        (90 days → removed).
      - Removed + compliant → re-admitted fresh (Major if qualifying).
      - Untracked + not compliant → not added (state stays bounded).
    """
    new_state = dict(state)
    counts = {"fresh": 0, "readmitted": 0, "promoted": 0, "demoted": 0,
              "expired": 0, "unchanged": 0, "tracked": 0}
    sp500_removed = sp500_removed or set()

    for ticker, facts in facts_by_ticker.items():
        ticker = ticker.upper()
        row = state.get(ticker)
        maj_now = major_qualifying(facts)
        compliant = league_compliant(facts)

        # ── Fresh entry (never tracked) ──────────────────────────────────
        if row is None:
            if not compliant:
                continue  # not eligible, not tracked — nothing to do
            league = config.LEAGUE_MAJOR if maj_now else config.LEAGUE_MINOR
            new_state[ticker] = {
                "ticker": ticker, "league": league,
                "consecutive_compliant": 0 if league == config.LEAGUE_MAJOR else 1,
                "consecutive_noncompliant": 0,
                "first_seen": as_of, "last_seen": as_of,
                # v4.6: tenure anchor — the day this stint became Major
                "major_since": as_of if league == config.LEAGUE_MAJOR else None}
            counts["fresh"] += 1
            counts["tracked"] += 1
            continue

        # ── Re-admission (was removed, now compliant again) ──────────────
        if row["league"] == config.LEAGUE_REMOVED:
            if not compliant:
                continue  # dormant; data preserved
            league = config.LEAGUE_MAJOR if maj_now else config.LEAGUE_MINOR
            new_state[ticker] = {
                "ticker": ticker, "league": league,
                "consecutive_compliant": 0 if league == config.LEAGUE_MAJOR else 1,
                "consecutive_noncompliant": 0,
                "first_seen": as_of, "last_seen": as_of,
                "major_since": as_of if league == config.LEAGUE_MAJOR else None}
            counts["readmitted"] += 1
            counts["tracked"] += 1
            continue

        # ── SP500 removal → non-SP500 rule kicks in (fresh recompute) ────
        if row["league"] == config.LEAGUE_MAJOR and ticker in sp500_removed:
            # Loses the index ticket; re-judged by cap alone, from scratch.
            if maj_now:                      # cap > $75B → still Major
                next_league = config.LEAGUE_MAJOR
                new_cc, new_nc = 0, 0
                counts["unchanged"] += 1
            elif compliant:                  # $50B < cap ≤ $75B → fresh Minor
                next_league = config.LEAGUE_MINOR
                new_cc, new_nc = 1, 0
                counts["demoted"] += 1
            else:                            # cap ≤ $50B → noncompliance clock
                next_league = config.LEAGUE_MINOR
                new_cc, new_nc = 0, 1
                counts["demoted"] += 1
            counts["tracked"] += 1
            new_state[ticker] = {
                "ticker": ticker, "league": next_league,
                "consecutive_compliant": new_cc, "consecutive_noncompliant": new_nc,
                "first_seen": row["first_seen"], "last_seen": as_of,
                # demotion clears the tenure anchor; staying Major keeps it
                "major_since": row.get("major_since")
                               if next_league == config.LEAGUE_MAJOR else None}
            continue

        # ── Normal transition (tracked major/minor) ──────────────────────
        cc = int(row["consecutive_compliant"])
        nc = int(row["consecutive_noncompliant"])
        new_cc, new_nc = advance_tenure(row["league"], compliant, cc, nc)
        next_league = transition(row["league"], maj_now, compliant, new_cc, new_nc)
        if next_league != row["league"]:
            counts[{"minor": "promoted", "major": "demoted",
                    "removed": "expired"}.get(next_league, "unchanged")] += 1
        else:
            counts["unchanged"] += 1
        counts["tracked"] += 1
        # v4.6 tenure anchor: stamped when entering/promoting INTO Major,
        # cleared on demotion; unchanged-Major days preserve it.
        if next_league == config.LEAGUE_MAJOR:
            major_since = (row.get("major_since") or as_of) \
                if row["league"] == config.LEAGUE_MAJOR else as_of
        else:
            major_since = None
        new_state[ticker] = {
            "ticker": ticker, "league": next_league,
            "consecutive_compliant": new_cc, "consecutive_noncompliant": new_nc,
            "first_seen": row["first_seen"], "last_seen": as_of,
            "major_since": major_since}
    return new_state, counts
