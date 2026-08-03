"""
Option OI snapshot store (v2.4 Phase 2).

Per-contract OI+vol history — the only way to see Burry-style accumulation,
since no vendor (yfinance or Moomoo) provides per-contract historical OI.

Patterns follow db.py (Alpha Terminal SQLite conventions). Data lives in
data/option_oi.db. Signals are fail-open: no history -> None -> score
falls back to base weights (option_screener.score_contract).
"""
import datetime
import os
import sqlite3

import config

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "option_oi.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS contract_oi (
  date TEXT, ticker TEXT, expiry TEXT, strike REAL, type TEXT,
  oi INTEGER, vol INTEGER, mid REAL, ts INTEGER,
  PRIMARY KEY (date, ticker, expiry, strike, type));
CREATE INDEX IF NOT EXISTS idx_contract_oi ON contract_oi (ticker, expiry, strike, type, date);
CREATE TABLE IF NOT EXISTS ticker_spot (
  date TEXT, ticker TEXT, spot REAL, ts INTEGER,
  PRIMARY KEY (date, ticker));
"""


def get_conn():
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def store_snapshot(date_str, ticker, spot, contracts):
    """Upsert one day's snapshot for a ticker. contracts = [(expiry, strike, type, oi, vol, mid), ...].
    Idempotent: re-running the same date replaces rows (PK on date+ticker+contract)."""
    conn = get_conn()
    try:
        conn.executescript(_SCHEMA)
        if spot:
            conn.execute("INSERT OR REPLACE INTO ticker_spot (date, ticker, spot, ts) VALUES (?,?,?,?)",
                         (date_str, ticker, spot, int(datetime.datetime.now().timestamp())))
        conn.executemany(
            "INSERT OR REPLACE INTO contract_oi (date, ticker, expiry, strike, type, oi, vol, mid, ts) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [(date_str, ticker, exp, strike, typ, oi, vol, mid,
              int(datetime.datetime.now().timestamp())) for (exp, strike, typ, oi, vol, mid) in contracts])
        conn.commit()
        return len(contracts)
    finally:
        conn.close()


def load_ticker_history(ticker):
    """One query per ticker: {(expiry, strike, type): [(date, oi, vol), ...]} + spot history."""
    conn = get_conn()
    try:
        conn.executescript(_SCHEMA)
        rows = conn.execute(
            "SELECT date, expiry, strike, type, oi, vol FROM contract_oi "
            "WHERE ticker=? ORDER BY date", (ticker,)).fetchall()
        hist = {}
        for r in rows:
            hist.setdefault((r["expiry"], r["strike"], r["type"]), []).append((r["date"], r["oi"], r["vol"]))
        spots = [(r["date"], r["spot"]) for r in conn.execute(
            "SELECT date, spot FROM ticker_spot WHERE ticker=? ORDER BY date", (ticker,)).fetchall()]
        return hist, spots
    finally:
        conn.close()


def _anchor(history, window_days):
    """Value at the latest date >= window_days before the NEWEST row (strictly older).
    Returns (date, oi, vol) or None when no older row qualifies."""
    if not history:
        return None
    latest_date = history[-1][0]
    cutoff = (datetime.date.fromisoformat(latest_date) - datetime.timedelta(days=window_days)).isoformat()
    for date_str, oi, vol in history:
        if cutoff <= date_str < latest_date:
            return date_str, oi, vol
    return None


def oi_build_pct(history, window_days):
    """(oi_newest - oi_anchor) / oi_anchor. None when anchor missing or oi_anchor <= 0."""
    if not history:
        return None
    anchor = _anchor(history, window_days)
    if anchor is None or anchor[1] is None or anchor[1] <= 0:
        return None
    newest_oi = history[-1][1] or 0
    return (newest_oi - anchor[1]) / anchor[1]


def vol_percentile(history):
    """Today's vol rank (0-100) vs its own history (min 3 points). None otherwise."""
    if not history or len(history) < config.OI_MIN_HISTORY_DAYS:
        return None
    vols = [v for (_, _, v) in history if v is not None]
    if len(vols) < config.OI_MIN_HISTORY_DAYS:
        return None
    today = vols[-1]
    return round(sum(1 for v in vols if v <= today) / len(vols) * 100, 1)


def _spot_change_pct(spots, window_days):
    """(spot_newest - spot_anchor)/spot_anchor over the same anchor window."""
    if not spots or len(spots) < 2:
        return None
    anchor = _anchor([(d, s, 0) for (d, s) in spots], window_days)
    if anchor is None or not anchor[1]:
        return None
    return (spots[-1][1] - anchor[1]) / anchor[1]


def build_signals(history, spots, window=5):
    """OI build % + vol percentile + divergence flag for one contract. Fail-open: None fields."""
    if not history or len(history) < config.OI_MIN_HISTORY_DAYS:
        return None
    sig = {"oi_build_5d": None, "vol_pctile": None, "divergence": False}
    for w in config.OI_BUILD_WINDOWS:
        sig[f"oi_build_{w}d"] = oi_build_pct(history, w)
    sig["vol_pctile"] = vol_percentile(history)
    b5 = sig.get("oi_build_5d")
    if b5 is not None and b5 >= config.OI_DIVERGENCE_BUILD:
        spot_chg = _spot_change_pct(spots, window)
        sig["divergence"] = spot_chg is None or abs(spot_chg) <= config.OI_DIVERGENCE_SPOT
    return sig
