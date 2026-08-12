"""
drift_alert.py — Quarterly + event-driven drift alerts with NS-2 regime gating.

Implements the frontier specification (NS-2 regime gating matrix + urgency
classification + summary format). Pure function — no API calls.

Recommendation strings MUST match the spec exactly — the PM reads these.
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

import config

log = logging.getLogger("ns6.drift_alert")

# ── NS-2 regime gating matrix (frontier-specified) ─────────────────────
# (state, direction) → recommendation string
_REC_OVERWEIGHT = {
    "TRENDING": "MONITOR. Don't trim while trending.",
    "MEAN_REV": "Consider trim. NS-2 neutral.",
    "CRISIS": "Reduce. Regime hostile.",
    "NO-EDGE": "Consider trim. NS-2 uncertain.",
}
_REC_UNDERWEIGHT = {
    "TRENDING": "Opportunity. Regime favorable.",
    "MEAN_REV": "Wait for regime confirmation.",
    "CRISIS": "Avoid. Regime hostile.",
    "NO-EDGE": "Wait. NS-2 uncertain.",
}
_REC_REMOVAL = {
    "TRENDING": "Remove from universe but timing TBD.",
    "MEAN_REV": "Remove. Screener dropped. NS-2 neutral.",
    "CRISIS": "Remove immediately. Both signals say exit.",
    "NO-EDGE": "Remove. No evidence to stay.",
}
_URGENCY_ORDER = {"URGENT": 0, "RECOMMENDED": 1, "CONSIDER": 2, "MONITOR": 3}


def _normalise_regime(value) -> str:
    """Normalise an NS-2 regime value to one of the 4 known states."""
    if not value:
        return "MEAN_REV"
    v = str(value).strip().upper()
    if v in ("TRENDING", "MEAN_REV", "CRISIS", "NO-EDGE"):
        return v
    return "MEAN_REV"  # unknown → conservative default


def run_drift_check(current_weights, target_weights, ns2_regimes=None,
                    theta=None) -> Dict:
    """Compute drift alerts for current vs target weights.

    Returns {"alerts": [...], "summary": str, "as_of": iso_date}.
    """
    theta = theta or config.load_theta()
    da = theta["drift_alert"]
    current_weights = current_weights or {}
    target_weights = target_weights or {}
    ns2_regimes = ns2_regimes or {}

    band_warning = da["band_rel_warning"] * 100
    band_urgent = da["band_rel_urgent"] * 100

    alerts = []
    all_tickers = set(current_weights) | set(target_weights)

    for ticker in sorted(all_tickers):
        cur = current_weights.get(ticker, 0.0)
        tgt = target_weights.get(ticker, 0.0)

        # delta_pct: (cur - tgt)/tgt * 100; target==0 → +100 (removal)
        if tgt == 0:
            delta_pct = 100.0
        else:
            delta_pct = (cur - tgt) / tgt * 100.0

        if abs(delta_pct) < band_warning:
            continue  # within band — no alert

        is_removal = tgt == 0
        direction = "overweight" if delta_pct >= 0 else "underweight"

        # NS-2 regime (normalise, default MEAN_REV)
        ns2 = ns2_regimes.get(ticker)
        if ns2 is None:
            regime = "MEAN_REV"
        elif isinstance(ns2, dict):
            regime = _normalise_regime(ns2.get("regime"))
        elif isinstance(ns2, (list, tuple)):
            regime = _normalise_regime(ns2[0] if len(ns2) > 0 else None)
        else:
            regime = _normalise_regime(ns2)

        # Recommendation from gating matrix
        if is_removal:
            rec = _REC_REMOVAL[regime]
        elif direction == "overweight":
            rec = _REC_OVERWEIGHT[regime]
        else:
            rec = _REC_UNDERWEIGHT[regime]

        # Urgency
        if abs(delta_pct) >= band_urgent:
            urgency = "URGENT"
        elif regime == "CRISIS" and direction == "overweight":
            urgency = "URGENT"
        elif regime == "TRENDING" and direction == "underweight":
            urgency = "RECOMMENDED"
        elif direction == "overweight" and regime == "MEAN_REV":
            urgency = "CONSIDER"
        else:
            urgency = "MONITOR"

        alerts.append({
            "ticker": ticker,
            "current_wt": round(cur, 4),
            "target_wt": round(tgt, 4),
            "delta_pct": round(delta_pct, 1),
            "direction": direction,
            "ns2_regime": regime,
            "recommendation": rec,
            "urgency": urgency,
            "is_removal": bool(is_removal),
        })

    # Sort: URGENT > RECOMMENDED > CONSIDER > MONITOR; then |delta_pct| desc
    alerts.sort(key=lambda a: (_URGENCY_ORDER[a["urgency"]], -abs(a["delta_pct"])))

    # Summary string
    actions = sum(1 for a in alerts if a["urgency"] in ("RECOMMENDED", "URGENT"))
    flagged = len(alerts)
    urgent = sum(1 for a in alerts if a["urgency"] == "URGENT")
    if actions == 0 and flagged == 0:
        summary = "Portfolio at target. No drift detected."
    else:
        summary = f"{actions} action{'s' if actions != 1 else ''} recommended, "
        summary += f"{flagged} position{'s' if flagged != 1 else ''} flagged"
        if urgent > 0:
            summary += f", {urgent} urgent"

    return {
        "alerts": alerts,
        "summary": summary,
        "as_of": datetime.now().strftime("%Y-%m-%d"),
    }


def check_event_driven_drift(current_weights, target_weights, theta=None) -> List[Dict]:
    """Return drift alerts for positions exceeding the URGENT band only.

    Returns empty list if none exceed the threshold.
    """
    theta = theta or config.load_theta()
    band_urgent = theta["drift_alert"]["band_rel_urgent"] * 100
    current_weights = current_weights or {}
    target_weights = target_weights or {}

    urgent_tickers = {}
    for ticker in set(current_weights) | set(target_weights):
        cur = current_weights.get(ticker, 0.0)
        tgt = target_weights.get(ticker, 0.0)
        if tgt == 0:
            delta_pct = 100.0 if cur > 0 else 0.0
        else:
            delta_pct = (cur - tgt) / tgt * 100.0
        if abs(delta_pct) >= band_urgent:
            urgent_tickers[ticker] = tgt

    if not urgent_tickers:
        return []

    # Re-run drift check but restricted to urgent tickers' target set.
    result = run_drift_check(
        {t: current_weights.get(t, 0.0) for t in urgent_tickers},
        urgent_tickers,
        theta=theta,
    )
    return result["alerts"]
