#!/usr/bin/env python3
"""
NS-5 Portfolio & Policy Store — JSON-file-backed CRUD.

Data layout:
  data/portfolios.json  {name: {ticker: shares}}           — v1: holdings as SHARES
                        {name: {ticker: {shares, account, lots}}} — v2: + tax metadata
  data/policies.json    {name: {ticker: weight}}      — allocation as WEIGHTS

v2 position schema (tax axis):
  {
    "AAPL": {
      "shares": 140,
      "account": "taxable",          # taxable | ira | roth | 401k (default taxable)
      "lots": [                       # optional — absent = fail-open (TLH N/A)
        {"date": "2024-03-01", "shares": 80, "cost_per_share": 171.2}
      ]
    }
  }
v1 flat {ticker: shares} loads as a single unknown-date lot (fail-open).

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


def _normalize_position(tk: str, value) -> Dict:
    """Normalize a stored position to v2 schema.

    v1 flat {ticker: shares} → {"shares", "account": "taxable", "lots": []}
    v2 {ticker: {shares, account, lots}} → passes through (defaults applied).
    """
    if isinstance(value, (int, float)):
        return {"shares": float(value), "account": "taxable", "lots": []}
    if isinstance(value, dict):
        shares = float(value.get("shares", 0))
        account = str(value.get("account", "taxable")).lower()
        if account not in ("taxable", "ira", "roth", "401k"):
            account = "taxable"
        lots = value.get("lots") or []
        return {"shares": shares, "account": account, "lots": list(lots)}
    return {"shares": 0.0, "account": "taxable", "lots": []}


def get_portfolio_positions(name: str) -> Optional[Dict[str, Dict]]:
    """Return a portfolio as v2 positions {ticker: {shares, account, lots}}.

    None if the portfolio doesn't exist. Fail-open: v1 entries normalize to
    flat lots=[] (TLH/basis sub-axes will grade N/A, never block composite).
    """
    entry = _load(PORTFOLIOS_PATH).get(name)
    if entry is None:
        return None
    return {str(tk).strip().upper(): _normalize_position(tk, v) for tk, v in entry.items()}


def upsert_portfolio(name: str, holdings: Dict[str, float]) -> Dict:
    """Create or update. Accepts v1 flat {ticker: shares} OR v2
    {ticker: {shares, account, lots}}. Returns the saved entry (v2-normalized)."""
    name = (name or "").strip()
    if not name:
        raise ValueError("portfolio name is required")
    if not isinstance(holdings, dict) or not holdings:
        raise ValueError("holdings must be a non-empty dict of {ticker: shares}")
    cleaned = {}
    for tk, value in holdings.items():
        tk = str(tk).strip().upper()
        if not tk:
            continue
        pos = _normalize_position(tk, value)
        if pos["shares"] < 0:
            raise ValueError(f"negative shares for {tk}")
        if pos["shares"] <= 0:
            continue  # drop zero-share positions
        # Store minimal v2: omit default account and empty lots
        entry = {"shares": pos["shares"]}
        if pos["account"] != "taxable":
            entry["account"] = pos["account"]
        if pos["lots"]:
            entry["lots"] = pos["lots"]
        cleaned[tk] = entry
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