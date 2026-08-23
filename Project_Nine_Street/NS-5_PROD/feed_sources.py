#!/usr/bin/env python3
"""feed_sources.py — NS-5 v4.5 source feed loader (DPF-owned construction logic).

Resolves a grade source into a weighted book NS-5 can grade:
  D1    → NS-7 DeltaOne basket (d1_basket.json weights)
  NS8   → NS-8 tactical signals (signals.json weights)
  NSETF → NS-ETF combined signals (signals.json weights)
  ALL   → merged fund book = union of the three, overlap SUMMED then normalized

Feed contract (house):
  - A source that is missing OR stale (> FEED_STALE_DAYS from its as_of) is
    treated as ABSENT — contributes nothing.
  - If ALL is selected, the surviving non-stale sources carry the book
    (fail-open); if every source is stale/absent, ALL → {} (no book).
  - A single selected source that is stale/absent → {} (no book, no grade).
  - Weights are normalized to sum 1.0 (post merge / post read).

This is DPF-owned methodology. Cheap-model (Ox) work must never modify the
weighting / fail-open / staleness semantics here.
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Optional

import config

log = logging.getLogger("ns5.feed_sources")


def _read_json(path: Path) -> Optional[Dict]:
    """Read a JSON file fail-open. None on missing/corrupt."""
    try:
        with open(path) as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else None
    except Exception as exc:  # noqa: BLE001 — fail-open on any read error
        log.warning("feed %s unreadable (%s) — treated as absent", path.name, exc)
        return None


def _as_of_date(doc: Dict) -> Optional[date]:
    """Parse the feed's as_of date. None if missing/unparseable."""
    for key in ("as_of", "selection_as_of"):
        raw = doc.get(key)
        if raw:
            try:
                return date.fromisoformat(str(raw)[:10])
            except ValueError:
                continue
    return None


def _is_stale(doc: Dict, stale_days: Optional[int] = None) -> bool:
    """True if the feed's as_of is older than stale_days (default config)."""
    stale_days = stale_days if stale_days is not None else config.FEED_STALE_DAYS
    as_of = _as_of_date(doc)
    if as_of is None:
        return True  # no as_of → treat as stale (fail-open to absent)
    return date.today() - as_of > timedelta(days=stale_days)


def _weights_from(doc: Dict) -> Dict[str, float]:
    """Extract a normalized {ticker: weight} book from a feed doc.

    Accepts: flat `weights` ({ticker: weight}) — the common NS-8/NS-ETF/D1 shape.
    Drops non-positive weights. Normalizes to sum 1.0.
    """
    raw = doc.get("weights") or {}
    if not isinstance(raw, dict) or not raw:
        return {}
    weights = {str(t): float(w) for t, w in raw.items() if w and float(w) > 0}
    total = sum(weights.values())
    if total <= 0:
        return {}
    return {t: w / total for t, w in weights.items()}


def _load_single(source: str,
                 stale_days: Optional[int] = None) -> Dict[str, float]:
    """Load one single source's weighted book. {} if missing/stale.

    source ∈ {"D1", "NS8", "NSETF"}. Raises ValueError on unknown source.
    """
    if source == "D1":
        path = config.D1_BASKET_PATH
    elif source == "NS8":
        path = config.NS8_SIGNALS_PATH
    elif source == "NSETF":
        path = config.NSETF_SIGNALS_PATH
    else:
        raise ValueError(f"unknown single source: {source}")

    doc = _read_json(path)
    if doc is None or _is_stale(doc, stale_days):
        return {}
    return _weights_from(doc)


def _merge(books: Dict[str, Dict[str, float]]) -> Dict[str, float]:
    """Merge source books into one: overlap SUMMED, then normalized to 1.0.

    Overlap must SUM, not clobber (house rule) — a ticker in two sources keeps
    both contributions. Fail-open: empty in → {}.
    """
    merged: Dict[str, float] = {}
    for book in books.values():
        for t, w in book.items():
            merged[t] = merged.get(t, 0.0) + w
    total = sum(merged.values())
    if total <= 0:
        return {}
    return {t: w / total for t, w in merged.items()}


def load_source(source: str,
                stale_days: Optional[int] = None) -> Dict[str, float]:
    """Resolve a grade source into a normalized weighted book.

    source ∈ config.FEED_SOURCES ("D1", "NS8", "NSETF", "ALL").
    - Single source → its own book, or {} if missing/stale.
    - ALL → merged union of the non-stale sources; {} if none available.
    Raises ValueError on an unknown source key.
    """
    source = str(source).strip().upper()
    if source == "ALL":
        books = {s: _load_single(s, stale_days) for s in ("D1", "NS8", "NSETF")}
        return _merge(books)
    if source in ("D1", "NS8", "NSETF"):
        return _load_single(source, stale_days)
    raise ValueError(
        f"unknown source '{source}'; expected one of {list(config.FEED_SOURCES)}")


def source_availability(stale_days: Optional[int] = None) -> Dict[str, bool]:
    """Per-source freshness for the dropdown/health (D1/NS8/NSETF only)."""
    return {s: bool(load_source(s, stale_days)) for s in ("D1", "NS8", "NSETF")}
