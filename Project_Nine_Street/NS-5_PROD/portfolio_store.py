#!/usr/bin/env python3
"""
NS-5 Portfolio & Policy Store — JSON-file-backed CRUD.

Data layout:
  data/portfolios.json  {name: {ticker: shares}}      — holdings as SHARES
  data/policies.json    {name: {ticker: weight}}      — allocation as WEIGHTS

File-backed (not SQLite) for v1: small state, human-readable, atomic writes.
Fail-open: missing/corrupt file → empty store, never crash.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Dict, List, Optional

import config

log = logging.getLogger("ns5.store")

PORTFOLIOS_PATH = config.DATA_DIR / "portfolios.json"
POLICIES_PATH = config.DATA_DIR / "policies.json"

# Seed data on first run (only when file is absent)
DEFAULT_PORTFOLIOS = {
    "Tech Heavy": {
        "AAPL": 140, "MSFT": 120, "NVDA": 80, "GOOGL": 70,
        "AMZN": 60, "META": 50, "TSLA": 40,
        "JPM": 50, "UNH": 40, "XOM": 50, "TLT": 300,
    },
    "Balanced 60/40": {
        "SPY": 60, "TLT": 400,
    },
}
DEFAULT_POLICIES = {
    "60/40 SPY/TLT": {"SPY": 0.60, "TLT": 0.40},
    "70/30 SPY/TLT": {"SPY": 0.70, "TLT": 0.30},
}


# ---------------------------------------------------------------------------
# Low-level file IO (atomic, fail-open)
# ---------------------------------------------------------------------------

def _load(path) -> Dict:
    if not path.exists():
        return {}
    try:
        with open(path) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001 — fail-open on corrupt file
        log.warning("store %s unreadable (%s) — starting empty", path.name, exc)
        return {}


def _save(path, data: Dict) -> None:
    """Atomic write: temp file in same dir + os.replace."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(config.DATA_DIR), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ---------------------------------------------------------------------------
# Portfolios (ticker → shares)
# ---------------------------------------------------------------------------

def list_portfolios() -> List[str]:
    return sorted(_load(PORTFOLIOS_PATH).keys())


def get_portfolio(name: str) -> Optional[Dict]:
    return _load(PORTFOLIOS_PATH).get(name)


def upsert_portfolio(name: str, holdings: Dict[str, float]) -> Dict:
    """Create or update. Returns the saved entry."""
    name = (name or "").strip()
    if not name:
        raise ValueError("portfolio name is required")
    if not isinstance(holdings, dict) or not holdings:
        raise ValueError("holdings must be a non-empty dict of {ticker: shares}")
    cleaned = {}
    for tk, shares in holdings.items():
        tk = str(tk).strip().upper()
        if not tk:
            continue
        shares = float(shares)
        if shares < 0:
            raise ValueError(f"negative shares for {tk}")
        cleaned[tk] = shares
    if not cleaned:
        raise ValueError("holdings must contain at least one ticker with positive shares")

    store = _load(PORTFOLIOS_PATH)
    store[name] = cleaned
    _save(PORTFOLIOS_PATH, store)
    return {"name": name, "holdings": cleaned}


def delete_portfolio(name: str) -> bool:
    """Delete by exact name. Returns True if existed."""
    store = _load(PORTFOLIOS_PATH)
    if name not in store:
        return False
    del store[name]
    _save(PORTFOLIOS_PATH, store)
    return True


def rename_portfolio(old_name: str, new_name: str) -> Optional[Dict]:
    """Rename preserving holdings. Returns new entry or None if old missing."""
    store = _load(PORTFOLIOS_PATH)
    if old_name not in store:
        return None
    new_name = (new_name or "").strip()
    if not new_name:
        raise ValueError("portfolio name is required")
    holdings = store.pop(old_name)
    store[new_name] = holdings
    _save(PORTFOLIOS_PATH, store)
    return {"name": new_name, "holdings": holdings}


# ---------------------------------------------------------------------------
# Policies (ticker → weight)
# ---------------------------------------------------------------------------

def list_policies() -> List[str]:
    return sorted(_load(POLICIES_PATH).keys())


def get_policy(name: str) -> Optional[Dict]:
    return _load(POLICIES_PATH).get(name)


def upsert_policy(name: str, weights: Dict[str, float]) -> Dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("policy name is required")
    if not isinstance(weights, dict) or not weights:
        raise ValueError("weights must be a non-empty dict of {ticker: weight}")
    store = _load(POLICIES_PATH)
    store[name] = dict(weights)
    _save(POLICIES_PATH, store)
    return {"name": name, "weights": dict(weights)}


def delete_policy(name: str) -> bool:
    store = _load(POLICIES_PATH)
    if name not in store:
        return False
    del store[name]
    _save(POLICIES_PATH, store)
    return True


# ---------------------------------------------------------------------------
# Seeding (first run only)
# ---------------------------------------------------------------------------

def seed_if_missing() -> None:
    """Populate default portfolios/policies when the store files don't exist."""
    if not PORTFOLIOS_PATH.exists():
        try:
            _save(PORTFOLIOS_PATH, DEFAULT_PORTFOLIOS)
            log.info("seeded default portfolios: %s", list(DEFAULT_PORTFOLIOS))
        except Exception as exc:  # noqa: BLE001
            log.warning("could not seed portfolios: %s", exc)
    if not POLICIES_PATH.exists():
        try:
            _save(POLICIES_PATH, DEFAULT_POLICIES)
            log.info("seeded default policies: %s", list(DEFAULT_POLICIES))
        except Exception as exc:  # noqa: BLE001
            log.warning("could not seed policies: %s", exc)