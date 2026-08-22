#!/usr/bin/env python3
"""NS-ETF parallel-run scorecard (weekly snapshot).

Compares NS-ETF's live signals against NS-1 (ETF rotation) and NS-4
(ratio signals) while the legacy services run their parallel-run quarter.
Appends one dated row per run to data/parallel_run_scorecard.json so the
quarter-end retirement decision is evidence-based.

Read-only over localhost HTTP + the local NS-ETF feed. Fail-open per
service: a down legacy service logs 'unavailable', never crashes the run.
CLT py3.9, env -u PYTHONPATH. Weekly launchd: Mondays 09:00.
"""
import json
import sys
import urllib.request
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

SCORECARD = config.DATA_DIR / "parallel_run_scorecard.json"
NS1_URL = "http://127.0.0.1:9218/api/signals"
NS4_URL = "http://127.0.0.1:9240/api/v1/rankings"


def fetch(url, timeout=15):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001 — fail-open per service
        return {"_error": str(e)}


def nsetf_state():
    """(weights, top_picks, crisis, vix_state) from the NS-ETF feed."""
    try:
        doc = json.loads(config.SIGNALS_PATH.read_text())
    except (OSError, ValueError):
        return {}, [], None, None
    weights = {t: w for t, w in doc.get("weights", {}).items() if w > 0}
    picks = [s["ticker"] for s in sorted(
        doc.get("composite_scores", []), key=lambda x: x["score"],
        reverse=True)[:3]]
    return (weights, picks,
            doc.get("crisis_mode"), (doc.get("vix") or {}).get("state"))


def ns1_top(payload):
    sigs = payload.get("signals", [])
    return [s["ticker"] for s in sorted(
        sigs, key=lambda s: s.get("score", 0), reverse=True)[:3]]


def ns1_universe(payload):
    return [s["ticker"] for s in payload.get("signals", [])]


def ns4_summary(payload):
    rows = payload if isinstance(payload, list) else []
    up = [r["symbol"] for r in rows
          if str(r.get("signal", "")).startswith(("ENTER", "HOLD LONG"))
          and r.get("score", 0) >= 1]
    return {"bullish_count": len(up),
            "top": [r["symbol"] for r in rows[:3]],
            "down": [r["symbol"] for r in rows
                     if str(r.get("signal", "")).startswith("EXIT")]}


def overlap(a, b):
    return sorted(set(a) & set(b))


def main():
    week_of = date.today().isoformat()
    etf_w, etf_picks, crisis, vix_state = nsetf_state()

    ns1 = fetch(NS1_URL)
    ns1_ok = "_error" not in ns1
    ns1_top3 = ns1_top(ns1) if ns1_ok else []
    ns1_uni = ns1_universe(ns1) if ns1_ok else []

    ns4 = fetch(NS4_URL)
    ns4_ok = "_error" not in ns4
    ns4_sum = ns4_summary(ns4) if ns4_ok else {}

    # Agreement: how much of NS-ETF's book overlaps NS-1's scored universe?
    agree = overlap(list(etf_w), ns1_uni) if ns1_ok else []
    pick_agree = overlap(etf_picks, ns1_top3) if ns1_ok else []

    row = {
        "week_of": week_of,
        "ns_etf": {
            "weights": {t: round(w, 4) for t, w in etf_w.items()},
            "top_scored": etf_picks,
            "crisis_mode": crisis,
            "vix_state": vix_state,
        },
        "ns1": {
            "available": ns1_ok,
            "top3": ns1_top3,
            "universe_size": len(ns1_uni),
        },
        "agreement": {
            "etf_in_ns1_universe": agree,
            "etf_picks_also_ns1_top3": pick_agree,
            "pick_overlap_pct": round(100 * len(pick_agree) / max(1, len(etf_picks)), 1)
            if etf_picks else None,
        },
        "ns4_advisory": {
            "available": ns4_ok,
            **ns4_sum,
        },
    }

    card = []
    if SCORECARD.exists():
        try:
            card = json.loads(SCORECARD.read_text())
        except ValueError:
            card = []
    card = [r for r in card if r.get("week_of") != week_of]  # idempotent rerun
    card.append(row)
    SCORECARD.write_text(json.dumps(card, indent=2))

    print(f"scorecard {week_of}: "
          f"ns_etf={len(etf_w)} names crisis={crisis} | "
          f"ns1={'up' if ns1_ok else 'DOWN'} top3={ns1_top3} | "
          f"overlap={agree} | "
          f"ns4={'up' if ns4_ok else 'DOWN'} bullish={ns4_sum.get('bullish_count')}")

    # Notable divergence flag: NS-ETF crisis state vs NS-1 still long sectors
    if crisis and ns1_ok and any(t.startswith("XL") for t in ns1_top3):
        print("  ⚠ DIVERGENCE: NS-ETF in CRISIS but NS-1 top-3 still holds sectors — PM review")
    return 0


if __name__ == "__main__":
    sys.exit(main())
