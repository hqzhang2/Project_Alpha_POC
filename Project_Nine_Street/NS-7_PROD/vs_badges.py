"""vs_badges.py — read-side accessor for the daily vs_pass_badges.json snapshot.

Fail-open contract: missing/corrupt/stale file → None; callers render nothing
extra. Freshness: generated_at older than config.VS_BADGE_MAX_AGE_HOURS → None.
"""
import json
from datetime import datetime
from pathlib import Path

import config

_FRAMEWORKS = ("graham", "greenblatt", "lynch", "buffett")


def load_snapshot(path=None):
    """The full snapshot dict, or None when missing/corrupt/stale."""
    p = Path(path or str(config.VS_BADGES_PATH))
    if not p.exists():
        return None
    try:
        snap = json.loads(p.read_text())
        datetime.fromisoformat(snap["generated_at"])
    except Exception:
        return None
    age_h = (datetime.now() - datetime.fromisoformat(snap["generated_at"]))
    if age_h.total_seconds() / 3600.0 > config.VS_BADGE_MAX_AGE_HOURS:
        return None
    return snap


def ticker_entry(ticker, path=None):
    """One ticker's {"hmm","value_screen"} block, or None if absent/stale."""
    snap = load_snapshot(path)
    if not snap:
        return None
    return (snap.get("tickers") or {}).get((ticker or "").upper())


def passed_frameworks(entry):
    """Sorted list of framework names that passed, e.g. ["graham","lynch"]."""
    vs = (entry or {}).get("value_screen") or {}
    fw = vs.get("frameworks") or {}
    return [name for name in _FRAMEWORKS if (fw.get(name) or {}).get("pass")]


def hmm_signal(entry):
    """{"signal","color","gate"} or None on per-ticker HMM error."""
    h = (entry or {}).get("hmm") or {}
    return h if h.get("signal") else None
