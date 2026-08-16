"""allocator.py — NS-X run_once: fetch returns → momentum → weights → alloc.json.

Design §6.1/§7.3. Orchestrates registry + rotation into the strategy allocation
document that NS-5 consumes. Idempotent (writes strategy_alloc.json). No LLM in
the compute path. Fail-open: a missing/disabled strategy contributes weight 0 and
the survivors absorb it.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import config
import registry
import rotation

log = logging.getLogger("nsx.allocator")


def _roles() -> Dict[str, str]:
    return {s.id: s.role for s in registry.enabled_registry()}


def build_allocation(as_of: Optional[str] = None) -> Dict:
    """Fetch live return streams → risk-adjusted momentum → strategy weights."""
    as_of = as_of or datetime.now().strftime("%Y-%m-%d")
    enabled = registry.enabled_registry()
    roles = _roles()

    return_streams: Dict[str, list] = {}
    momentum_scores: Dict[str, Optional[float]] = {}
    sources: Dict[str, str] = {}
    for s in enabled:
        rets = registry.get_returns(s.id)
        return_streams[s.id] = rets
        sources[s.id] = s.return_stream
        momentum_scores[s.id] = (rotation.strategy_momentum(rets)
                                 if rets and len(rets) > 3 else None)

    weights = rotation.weight_strategies(momentum_scores, roles)

    doc = {
        "as_of": as_of,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "rotation": config.ROTATION,
        "risk_adjusted": config.RISK_ADJUST,
        "strategies": weights,
        "momentum_scores": {k: (round(v, 6) if v is not None else None)
                            for k, v in momentum_scores.items()},
        "return_sources": sources,
        "weights_sum": round(sum(weights.values()), 12),
        # honesty flags (NS-5 consumes these)
        "streams_differentiated": registry.streams_differentiated(),
        "stale_after_days": config.NSX_STALE_DAYS,
        "version": 1,
    }
    return doc


def _is_stale(path: Path, now: Optional[datetime] = None) -> bool:
    """True if the on-disk allocation is older than NSX_STALE_DAYS."""
    if not path.exists():
        return True
    now = now or datetime.now()
    try:
        doc = json.loads(path.read_text())
        gen = datetime.fromisoformat(doc.get("generated_at", ""))
        return (now - gen).days > config.NSX_STALE_DAYS
    except Exception:
        return True


def write_allocation(doc: Dict, path: Optional[Path] = None) -> Path:
    path = path or config.ALLOC_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, default=str))
    return path


def run_once(as_of: Optional[str] = None) -> Dict:
    """Full pipeline: build + persist the allocation. Returns the doc."""
    doc = build_allocation(as_of)
    write_allocation(doc)
    log.info("NS-X alloc %s: %s", doc["as_of"],
             {k: round(v, 3) for k, v in doc["strategies"].items()})
    return doc


if __name__ == "__main__":
    import sys
    sys.exit(0 if run_once() else 1)
