"""sleeve_blend.py — NS-5 joint-universe construction (2b, DESIGN §4.3).

The PM-facing target portfolio: growth sleeve (NS-7 momentum top-N) ∪ value
sleeve (A_T 4-framework agreement ≥ 2), sized by the regime-conditional tilt
(GDP×CPI axis, read from common.regime_store — R1/R2 growth, R3/R4 defensive).
Equal-weight WITHIN each sleeve (the 2a research basis); concentration
guardrails are REPORTED, not enforced here — the grading engine grades the
book and NS-6 owns enforcement.

Read-only consumers (house decoupled pattern):
  - NS-7 feed:   data/selection.json        (file read)
  - A_T screen:  /api/fundamentals/screen   (localhost HTTP, point-in-time)
  - regime:      common.regime_store        (SQLite, FRED+GDF classified)

Fail-open: any sleeve unavailable → the other sleeve carries 100% of the
book; never block construction on a data outage.

Output: data/sleeve_blend.json — the joint-universe target the PM decides on.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent.parent.parent   # repo root (common/)
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import config  # noqa: E402


# ── Sleeve reads (fail-open: [] on any outage) ───────────────────────────
def growth_sleeve(path: Optional[str] = None) -> List[str]:
    """NS-7 top-N momentum picks (selection.json). [] if missing/stale."""
    p = Path(path or str(config.NS7_SELECTION_PATH))
    try:
        doc = json.loads(p.read_text())
        return [s["ticker"] for s in doc.get("selections", [])]
    except (OSError, ValueError, TypeError):
        return []


def value_sleeve(url: Optional[str] = None, timeout: int = 15) -> List[str]:
    """A_T 4-framework agreement ≥ 2 picks (localhost HTTP). [] if down."""
    u = url or config.AT_SCREENER_URL
    try:
        with urllib.request.urlopen(u, timeout=timeout) as r:
            payload = json.loads(r.read().decode())
    except Exception:  # noqa: BLE001 — network/HTTP outage → fail-open
        return []
    rows = [r for r in payload.get("results", [])
            if r.get("agreement", 0) >= 2]
    return [r["ticker"] for r in rows][:config.VALUE_SLEEVE_N]


def regime_class() -> str:
    """GDP×CPI regime → 'growth' (R1/R2) | 'defensive' (R3/R4). Fail-open."""
    try:
        from common.regime_store import latest
        row = latest()
        reg = str(row.get("regime", "")) if row else ""
        return "growth" if reg in ("R1", "R2") else "defensive"
    except Exception:  # noqa: BLE001 — store missing/empty → defensive default
        return "defensive"


def etf_sleeve(path: Optional[str] = None, stale_days: int = 5
               ) -> Dict[str, float]:
    """NS-ETF blended weights (signals.json). {} if missing/stale.

    Staleness: as_of older than `stale_days` calendar days → treat as out
    (fail-open to the equity-only book rather than sizing on a dead feed).
    """
    p = Path(path or str(config.NSETF_SIGNALS_PATH))
    try:
        doc = json.loads(p.read_text())
    except (OSError, ValueError):
        return {}
    try:
        from datetime import date, timedelta
        as_of = date.fromisoformat(doc["as_of"])
        if date.today() - as_of > timedelta(days=stale_days):
            return {}
    except (KeyError, ValueError):
        return {}
    weights = {t: w for t, w in doc.get("weights", {}).items() if w > 0}
    total = sum(weights.values())
    return {t: w / total for t, w in weights.items()} if total > 0 else {}


def apply_etf_share(blended: Dict[str, float],
                    etf: Dict[str, float],
                    etf_share: float) -> Tuple[Dict[str, float], float]:
    """Scale the equity book by (1 − etf_share), then overlay the ETF book.

    Equity tilt ratio (momentum:value within the scaled equity block) is
    preserved. Returns (new_blended, applied_share); applied share shrinks
    fail-open when the ETF feed is thin (< 1 name → no-op).
    """
    if not etf or etf_share <= 0:
        return blended, 0.0
    scale = 1.0 - etf_share
    out = {t: w * scale for t, w in blended.items()}
    per = etf_share / len(etf)
    for t, w in etf.items():
        out[t] = out.get(t, 0.0) + per
    return out, etf_share


# ── Pure construction (unit-testable) ────────────────────────────────────
def build_blend(growth: List[str], value: List[str],
                tilt: Tuple[float, float]) -> Dict:
    """Sleeve weights × equal-weight within sleeves + guardrail stats.

    Fail-open contract: if one sleeve is unavailable, the surviving sleeve
    carries 100% of the book (fully invested, never a partial book).
    """
    w_mom, w_val = tilt
    if growth and not value:
        w_mom, w_val = 1.0, 0.0
    elif value and not growth:
        w_mom, w_val = 0.0, 1.0
    blended: Dict[str, float] = {}
    if growth:
        wm = w_mom / len(growth)
        for t in growth:
            blended[t] = blended.get(t, 0.0) + wm   # union: overlap sums
    if value:
        wv = w_val / len(value)
        for t in value:
            blended[t] = blended.get(t, 0.0) + wv
    wsum = sum(blended.values())
    eff_n = (1.0 / sum(w * w for w in blended.values())) if blended else 0.0
    return {
        "blended": blended,
        "guardrails": {
            "n": len(blended),
            "eff_n": round(eff_n, 2),
            "max_weight": round(max(blended.values()), 4) if blended else 0.0,
            "weights_sum": round(wsum, 4),
        },
    }


def main() -> int:
    growth = growth_sleeve()
    value = value_sleeve()
    etf = etf_sleeve()
    reg = regime_class()
    tilt = config.SLEEVE_TILT[reg]
    doc = build_blend(growth, value, tilt)
    blended, applied = apply_etf_share(
        doc["blended"], etf, config.ETF_SLEEVE_SHARE[reg])
    doc["blended"] = blended
    g = doc["guardrails"]
    if blended:
        g["eff_n"] = round(1.0 / sum(w * w for w in blended.values()), 2)
        g["max_weight"] = round(max(blended.values()), 4)
        g["weights_sum"] = round(sum(blended.values()), 4)
    out = {
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "service": "NS-5",
        "regime": reg,
        "tilt_table": config.SLEEVE_TILT,
        "sleeve_weights": {"momentum": tilt[0], "value": tilt[1],
                           "etf_share_applied": applied},
        "growth_sleeve": growth,
        "value_sleeve": value,
        "etf_sleeve": sorted(etf),
        **doc,
    }
    config.BLEND_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.BLEND_PATH.write_text(json.dumps(out, indent=2, default=str))
    print(f"blend {out['as_of']}: regime={reg} tilt={tilt[0]:.0%}/{tilt[1]:.0%} "
          f"mom={len(growth)} val={len(value)} etf={len(etf)} "
          f"(share {applied:.0%}) n={g['n']} effN={g['eff_n']} "
          f"maxW={g['max_weight']:.1%} → {config.BLEND_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
