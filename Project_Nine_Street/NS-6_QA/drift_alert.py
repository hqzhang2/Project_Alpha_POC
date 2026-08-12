"""
drift_alert.py — Quarterly + event-driven drift alerts with NS-2 regime gating.

FRONTIER SPECIFICATION (junior implements this exact classification logic).

────────────────────────────────────────────────────────────────────────
DRIFT CHECK ALGORITHM
────────────────────────────────────────────────────────────────────────

run_drift_check(current_weights, target_weights, ns2_regimes, theta)
    → list[DriftAlert]

  current_weights : dict[ticker → float]    — current portfolio weights
  target_weights  : dict[ticker → float]    — NS-5 frontier target weights
  ns2_regimes     : dict[ticker → str]      — NS-2 regime per ticker
  theta           : dict                    — from config.load_theta()


  COMPUTATION:

  1. For each ticker in current_weights ∪ target_weights:
     a. Compute delta_pct = (current - target) / target × 100
        (percentage difference from target)
        - Positive = overweight, negative = underweight.
        - If target == 0: delta_pct = +100 (ticker not in target universe —
          removal candidate, always flagged).

     b. Check band thresholds:
        abs_delta_pct = |delta_pct|
        if abs_delta_pct < theta["drift_alert"]["band_rel_warning"] * 100:
            CONTINUE (within band, no alert)

     c. Determine direction: "overweight" | "underweight"

     d. Query NS-2 regime for this ticker:
        ns2 = ns2_regimes.get(ticker)
        if ns2 is None:
            regime = "MEAN_REV"  # default fallback (conservative)
        else:
            regime = ns2.get("regime", "MEAN_REV")
            # Normalise: NS-2 returns "TRENDING"/"MEAN_REV"/"CRISIS"/"NO-EDGE"
            # Accept any casing, treat unknown as MEAN_REV.

     e. Generate recommendation from the NS-2 regime gating matrix:

        NS-2 REGIME GATING MATRIX (overweight positions):
        ┌────────────┬──────────────────────────────────────────┐
        │ NS-2 State │ Recommendation                           │
        ├────────────┼──────────────────────────────────────────┤
        │ TRENDING   │ "MONITOR. Don't trim while trending."    │
        │ MEAN_REV   │ "Consider trim. NS-2 neutral."           │
        │ CRISIS     │ "Reduce. Regime hostile."                │
        │ NO-EDGE    │ "Consider trim. NS-2 uncertain."         │
        └────────────┴──────────────────────────────────────────┘

        For UNDERWEIGHT positions:
        ┌────────────┬──────────────────────────────────────────┐
        │ NS-2 State │ Recommendation                           │
        ├────────────┼──────────────────────────────────────────┤
        │ TRENDING   │ "Opportunity. Regime favorable."         │
        │ MEAN_REV   │ "Wait for regime confirmation."          │
        │ CRISIS     │ "Avoid. Regime hostile."                 │
        │ NO-EDGE    │ "Wait. NS-2 uncertain."                  │
        └────────────┴──────────────────────────────────────────┘

        For REMOVAL candidates (target == 0, always overweight):
        ┌────────────┬──────────────────────────────────────────┐
        │ NS-2 State │ Recommendation                           │
        ├────────────┼──────────────────────────────────────────┤
        │ TRENDING   │ "Remove from universe but timing TBD."   │
        │ MEAN_REV   │ "Remove. Screener dropped. NS-2 neutral."│
        │ CRISIS     │ "Remove immediately. Both signals say    │
        │            │   exit."                                 │
        │ NO-EDGE    │ "Remove. No evidence to stay."           │
        └────────────┴──────────────────────────────────────────┘

     f. Determine urgency:
        abs_delta_pct >= theta["drift_alert"]["band_rel_urgent"] * 100
            → "URGENT"
        elif regime == "CRISIS" and direction == "overweight"
            → "URGENT"
        elif regime == "TRENDING" and direction == "underweight"
            → "RECOMMENDED"
        elif direction == "overweight" and regime == "MEAN_REV"
            → "CONSIDER"
        else:
            → "MONITOR"

     g. Build DriftAlert:
        {
            "ticker": ticker,
            "current_wt": current_wt,
            "target_wt": target_wt,
            "delta_pct": round(delta_pct, 1),
            "direction": direction,
            "ns2_regime": regime,
            "recommendation": recommendation_str,
            "urgency": urgency,
            "is_removal": target == 0,
        }

  2. Sort alerts: URGENT > RECOMMENDED > CONSIDER > MONITOR.
     Within same urgency: larger |delta_pct| first.

  3. Build summary string:
     actions = sum(1 for a in alerts if a["urgency"] in ("RECOMMENDED","URGENT"))
     flagged = len(alerts)
     urgent = sum(1 for a in alerts if a["urgency"] == "URGENT")
     summary = f"{actions} action{'s' if actions != 1 else ''} recommended, "
     summary += f"{flagged} position{'s' if flagged != 1 else ''} flagged"
     if urgent > 0:
         summary += f", {urgent} urgent"
     if actions == 0 and flagged == 0:
         summary = "Portfolio at target. No drift detected."

  4. Return {"alerts": alerts, "summary": summary, "as_of": today_iso}


  EVENT-DRIVEN TRIGGER (separate function):
  check_event_driven_drift(current_weights, target_weights, theta)
      → list[DriftAlert] or None

    For each position where |delta_pct| >= theta["drift_alert"]["band_rel_urgent"] * 100:
      Return run_drift_check() for just those positions.
    If no positions exceed the threshold: return empty list.

────────────────────────────────────────────────────────────────────────
JUNIOR IMPLEMENTATION NOTES
────────────────────────────────────────────────────────────────────────

- Pure function — no API calls. NS-2 regimes are pre-fetched by the caller
  (scenario.py or a cron job) and passed as a dict.
- The NS-2 regime gating matrix MUST be implemented exactly as specified
  above. Do not modify the recommendation strings.
- Urgency classification MUST use the bands and rules above.
- Summary string format: match the examples above exactly. PM reads these.
- All thresholds from config.py via theta["drift_alert"].
- Fail-open: if ns2_regimes is empty/None, all tickers default to MEAN_REV.
"""
