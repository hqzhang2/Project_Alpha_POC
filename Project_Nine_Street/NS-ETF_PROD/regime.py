"""NS-ETF regime — soft HMM confidence scaling (NS-3 T3 pattern).

Scales conviction; NEVER hard-gates. Deterministic via config.HMM_SEED.
Falls back to common.regime_store's GDP×CPI axis when available;
fail-open to NEUTRAL on any outage.
"""
import json
import math
import random
import sqlite3

import config


def _gaussian_hmm_1d(values, n_states=3, iters=30):
    """Minimal deterministic 1-D Gaussian HMM (Baum-Welch, seeded).
    Returns (state_of_last_value, means, confidence_in_[0,1]).
    Hand-rolled so we don't depend on hmmlearn (NS-3 lesson: import
    fragility across interpreters)."""
    if len(values) < 20:
        return None
    values = list(values)
    rng = random.Random(config.HMM_SEED)
    lo, hi = min(values), max(values)
    means = [lo + (hi - lo) * (i + 0.5) / n_states for i in range(n_states)]
    sds = [max((hi - lo) / n_states / 2, 1e-9)] * n_states

    resp = None
    for _ in range(iters):
        # E-step: responsibilities
        resp = [[0.0] * n_states for _ in values]
        for i, v in enumerate(values):
            w = [math.exp(-0.5 * ((v - m) / s) ** 2) / s for m, s in zip(means, sds)]
            tot = sum(w)
            resp[i] = [x / tot for x in w]
        # M-step
        for k in range(n_states):
            g = [resp[i][k] for i in range(len(values))]
            gt = sum(g)
            if gt == 0:
                continue
            means[k] = sum(gi * v for gi, v in zip(g, values)) / gt
            sds[k] = max(math.sqrt(sum(gi * (v - means[k]) ** 2
                                       for gi, v in zip(g, values)) / gt), 1e-9)
    if resp is None:
        return None
    last = resp[-1]
    state = max(range(n_states), key=lambda k: last[k])
    return state, means, last[state]


def classify(conn=None, momentum_series=None):
    """Returns {regime, confidence, source}. Soft — advisory only."""
    # Prefer the house regime store (GDP×CPI axis) as primary label.
    axis = None
    try:
        conn = sqlite3.connect(str(config.REGIME_STORE_PATH))
        row = conn.execute(
            "SELECT regime FROM regime ORDER BY date DESC LIMIT 1").fetchone()
        conn.close()
        if row:
            axis = {"regime": row[0], "source": "regime_store"}
    except Exception:
        axis = None

    if momentum_series and len(momentum_series) >= 20:
        res = _gaussian_hmm_1d(momentum_series)
        if res:
            state, means, conf = res
            trend_state = ["bear", "range", "bull"][state] if len(means) == 3 else str(state)
            out = {"hmm_state": trend_state,
                   "hmm_confidence": round(conf, 3),
                   "source": "local_hmm"}
            if axis:
                out.update(axis)
                out["source"] = "regime_store+local_hmm"
            return out

    return {"regime": "NEUTRAL", "confidence": None, "source": "fail_open"}


def scale_conviction(score, regime_info):
    """Soft scaling: multiply score by confidence-derived factor in
    [0.75, 1.25]. Never flips a sign, never zeroes a pick."""
    conf = regime_info.get("hmm_confidence") if isinstance(regime_info, dict) else None
    if not isinstance(conf, (int, float)):
        return score
    factor = 0.75 + 0.5 * float(conf)          # conf∈[0,1] → [0.75,1.25]
    return score * factor
