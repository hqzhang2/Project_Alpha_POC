"""NS-ETF overlay — VIX exposure cap, crisis rotation, cash floor (NS-1)."""
import config


def vix_state(vix_spot, vix_avg):
    """Classify VIX regime. Fail-open: missing data → NORMAL (logged upstream)."""
    if vix_spot is None:
        return {"state": "NORMAL", "exposure_cap": 1.0, "crisis": False,
                "reason": "vix unavailable"}
    avg = vix_avg if vix_avg is not None else vix_spot
    crisis = vix_spot >= config.VIX_CRISIS_LEVEL
    # Exposure cap scales down as spot exceeds its own average.
    ratio = vix_spot / avg if avg > 0 else 1.0
    cap = max(0.3, min(1.0, 2.0 - ratio))
    return {
        "state": "CRISIS" if crisis else ("ELEVATED" if ratio > 1.15 else "NORMAL"),
        "exposure_cap": round(cap, 3),
        "crisis": crisis,
        "spot": round(vix_spot, 2),
        "avg": round(avg, 2),
    }


def apply_overlay(conn, sleeve_picks, weights, vix_info):
    """Apply crisis rotation / cash floor / exposure cap.

    sleeve_picks: {sleeve: [ticker,...]} ranked picks
    weights:      {ticker: weight}   proposed inverse-vol weights
    Returns (final_weights, events) — events logged for dashboard/Why.
    """
    events = []
    final = dict(weights)

    if vix_info.get("crisis"):
        # Crisis: rotate the whole risk book into CRISIS_SAFE names,
        # inverse-vol among them; keep it deterministic.
        safe = sorted(config.CRISIS_SAFE)
        n = len(safe)
        w = {t: 1.0 / n for t in safe}
        tot = sum(w.values())
        final = {t: v / tot for t, v in w.items()}   # exact normalization
        events.append({"type": "crisis_rotation",
                       "detail": f"VIX >= {config.VIX_CRISIS_LEVEL} → rotated to CRISIS_SAFE"})
        return final, events

    cap = vix_info.get("exposure_cap", 1.0)
    if cap < 1.0:
        risky_total = sum(w for t, w in final.items() if t not in config.CRISIS_SAFE)
        scale = min(1.0, cap / risky_total) if risky_total > 0 else 1.0
        residual = 1.0 - sum(w * scale for t, w in final.items()
                             if t not in config.CRISIS_SAFE)
        final = {t: (w * scale if t not in config.CRISIS_SAFE else w)
                 for t, w in final.items()}
        if config.CASH_EQ in final or True:
            final[config.CASH_EQ] = final.get(config.CASH_EQ, 0.0) + residual
            events.append({"type": "exposure_cap",
                           "detail": f"cap={cap:.2f} → {config.CASH_EQ} floor {residual:.2%}"})

    # Normalize to exactly 1.0 (drift from rounding)
    tot = sum(final.values())
    if tot > 0:
        final = {t: w / tot for t, w in final.items()}
    return final, events
