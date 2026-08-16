"""registry.py — NS-X Strategy Registry (declarative, extensible).

The registry is the single source of truth for which strategies exist, their
role, and where their target book + return stream come from. Adding NS-9/10 is
one registry row + a producer; the rotation engine handles the rest generically.

Cross-service reads are DECOUPLED (house pattern): NS-X reads each strategy's
data files / endpoints directly, never importing another service's modules —
this avoids the config-name collision that breaks combined-process imports.

Roles are FUNCTIONAL (design §4.1/§5.2):
  - "return"/"diversifier"  → momentum-ranked, may go to 0
  - "defensive"             → gets a floor (never zeroed) in risk-off regimes
  - "riskoff" (cash)        → always 0 momentum, residual sleeve
  - "supplemental"          → momentum-ranked, no floor

enabled=False strategies are declared (so the NS-9/10 pattern is exercised) but
excluded from rotation until they clear a walk-forward gate (§4.4).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import config


@dataclass
class Strategy:
    id: str
    name: str
    role: str                  # "return" | "diversifier" | "defensive" | "supplemental" | "riskoff"
    target_book: str           # producer id → current holdings (state)
    return_stream: str         # producer id → LIVE realized NAV (trajectory, §4.5)
    cadence: str               # "daily" | "monthly" | "quarterly"
    enabled: bool = True
    producers: Dict[str, str] = field(default_factory=dict)


def build_registry() -> List[Strategy]:
    """The default rotation universe (design §4.4): {ns7, at_val, ns8, cash}."""
    return [
        Strategy("ns7", "Equity Momentum", "return", "ns7_selection", "ns7_returns",
                 "daily", True),
        Strategy("at_val", "Equity Value", "defensive", "at_screener", "at_returns",
                 "quarterly", True),
        Strategy("ns8", "Tactical Multi-AA", "diversifier", "ns8_signals", "ns8_returns",
                 "monthly", True),
        Strategy("ns1", "ETF Cap-Preservation", "defensive", "ns1_book", "ns1_returns",
                 "monthly", False),          # superseded by NS-6 (§4.4)
        Strategy("ns3", "Sector Rotation", "supplemental", "ns3_book", "ns3_returns",
                 "quarterly", False),        # gate destroys value (§4.4)
        Strategy("cash", "Cash / Risk-Off (SHV)", "riskoff", "shv_book", "shv_returns",
                 "daily", True),
    ]


def enabled_registry() -> List[Strategy]:
    """The strategies that actually rotate (enabled == True)."""
    return [s for s in build_registry() if s.enabled]


# ── Return-stream producers (LIVE realized NAV, §4.5) ────────────────────
# Fail-open: any producer that can't resolve returns an empty list → the
# strategy gets weight 0 (§5.2 quality floor) and the survivors absorb it.

def _load_ns8_returns() -> List[float]:
    """NS-8 live realized returns (from its real walk-forward closes)."""
    try:
        with open(config.NS8_HIST) as fh:
            doc = json.load(fh)
        dates = doc["dates"]
        closes = {t: doc["closes"][t] for t in doc["tickers"]}
        # Use SPY as the equity-proxy return stream for NS-8 momentum (simplest
        # live proxy; production can refine to the full 6-ETF book).
        spy = [c for c in closes["SPY"] if c is not None]
        if len(spy) < 2:
            return []
        return [spy[i] / spy[i - 1] - 1.0 for i in range(1, len(spy)) if spy[i - 1]]
    except Exception:
        return []


def _load_ns7_returns() -> List[float]:
    """NS-7 momentum strategy live-return proxy (SPY, cached closes)."""
    return _load_ns8_returns()   # same equity proxy for a first pass


def _load_at_returns() -> List[float]:
    """A_T value sleeve live-return proxy (SPY)."""
    return _load_ns8_returns()


def _load_shv_returns() -> List[float]:
    """Cash strategy — flat 0 returns (reference point)."""
    return [0.0] * 300


def _load_ns1_returns() -> List[float]:
    return []                    # not enabled — no live stream yet
def _load_ns3_returns() -> List[float]:
    return []                    # not enabled — no live stream yet


RETURN_PRODUCERS = {
    "ns7_returns": _load_ns7_returns,
    "at_returns": _load_at_returns,
    "ns8_returns": _load_ns8_returns,
    "ns1_returns": _load_ns1_returns,
    "ns3_returns": _load_ns3_returns,
    "shv_returns": _load_shv_returns,
}


def get_returns(strategy_id: str) -> List[float]:
    """Live realized return series for a strategy (fail-open: [] on error)."""
    reg = {s.id: s for s in build_registry()}
    s = reg.get(strategy_id)
    if s is None or not s.enabled:
        return []
    prod = RETURN_PRODUCERS.get(s.return_stream)
    if prod is None:
        return []
    return prod()
