#!/usr/bin/env python3
"""NS-UPRO weekly covered-call calculator — data-pull service.

Serves calculator.html and provides /api/pull (free data: yfinance closes,
RV20, rolling 2y RV20 terciles, VIX9D) and /api/log (IV Log accumulation).

Run:  venv/bin/python server.py   (default port 9311, env PORT overrides)
Pattern: stdlib ThreadingHTTPServer (see dashboard-development skill).
"""
import json
import os
import sys
import threading
import urllib.request
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "iv_log.json")
PORT = int(os.environ.get("PORT", "9311"))
LOG_LOCK = threading.Lock()   # serialize read-modify-write of iv_log.json

_cache = {"ts": 0.0, "payload": None, "key": None}


def _flatten(df):
    if hasattr(df, "columns") and getattr(df.columns, "nlevels", 1) > 1:
        df.columns = df.columns.get_level_values(0)
    return df


def pull_data(ticker):
    """Free-data pull: price history + VIX9D. Returns dict for the calculator."""
    import yfinance as yf
    import numpy as np

    end = datetime.now()
    start = end - timedelta(days=400)  # ~2y of trading days
    px = _flatten(yf.download(ticker, start=start.strftime("%Y-%m-%d"),
                              end=end.strftime("%Y-%m-%d"), auto_adjust=True,
                              progress=False))
    if px is None or len(px) < 60:
        raise RuntimeError("insufficient price history for %s" % ticker)

    closes = px["Close"].astype(float)
    opens = px["Open"].astype(float)

    # S0: today's open if today's bar exists intraday, else last close
    import pandas as _pd
    last_date = closes.index[-1]
    last_day = _pd.Timestamp(last_date).date()
    today = datetime.now().date()
    if last_day == today:
        s0 = float(opens.iloc[-1])
        rv_closes = closes.iloc[:-1]      # RV uses completed sessions only
    else:
        s0 = float(closes.iloc[-1])
        rv_closes = closes

    # RV20 series (annualized, ln returns)
    logret = np.log(rv_closes).diff().dropna()
    rv_series = logret.rolling(20).std() * float(np.sqrt(252))
    rv20 = float(rv_series.iloc[-1])

    # Rolling 2y tercile cutoffs (no lookahead: cutoffs from rv history itself)
    rv_hist = rv_series.dropna()
    p50 = float(rv_hist.quantile(0.50))
    p75 = float(rv_hist.quantile(0.75))

    # VIX9D (SPY 9-day IV) — markup sanity check
    try:
        v9 = _flatten(yf.download("^VIX9D", period="10d", progress=False))
        raw = float(v9["Close"].astype(float).iloc[-1])
        # Yahoo may return index points (~11.7) or a fraction (0.117)
        vix9d = raw if raw > 2.0 else raw * 100.0
    except Exception:
        vix9d = None

    return {
        "ticker": ticker,
        "as_of": str(last_day),
        "s0": round(s0, 4),
        "rv20": round(rv20, 6),
        "rv20_pct": round(rv20 * 100, 2),
        "p50_pct": round(p50 * 100, 2),
        "p75_pct": round(p75 * 100, 2),
        "vix9d_pct": round(vix9d, 2) if vix9d else None,
        "bars": int(len(closes)),
        "pulled_at": datetime.now().isoformat(timespec="seconds"),
    }


def load_log():
    if not os.path.exists(LOG_PATH):
        return []
    try:
        with open(LOG_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []  # corrupt/partial file (shouldn't happen w/ atomic write) -> treat as empty


def append_log(entry):
    with LOG_LOCK:
        log = load_log()
        # one entry per (week, ticker): replace if same ISO week
        iso = datetime.strptime(entry["date"], "%Y-%m-%d").isocalendar()
        week_key = "%d-W%02d" % (iso[0], iso[1])
        entry["week"] = week_key
        log = [e for e in log if not (e.get("week") == week_key and e.get("ticker") == entry.get("ticker"))]
        log.append(entry)
        log.sort(key=lambda e: e["date"])
        # atomic write-then-rename: readers never observe a partial file
        tmp = LOG_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(log, f, indent=2)
        os.replace(tmp, LOG_PATH)
        return log


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            with open(os.path.join(BASE_DIR, "calculator.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif path == "/health":
            self._json(200, {"status": "ok", "service": "NS-UPRO-calculator"})
        elif path == "/api/pull":
            qs = parse_qs(parsed.query)
            ticker = (qs.get("ticker") or ["UPRO"])[0].upper().strip()
            if ticker not in ("UPRO", "TQQQ"):
                self._json(400, {"error": "ticker must be UPRO or TQQQ"})
                return
            key = ticker + datetime.now().strftime("%Y%m%d%H%M")
            now = datetime.now().timestamp()
            if _cache["payload"] and _cache["key"] == key and now - _cache["ts"] < 60:
                self._json(200, dict(_cache["payload"], cached=True))
                return
            try:
                payload = pull_data(ticker)
                _cache.update(ts=now, payload=payload, key=key)
                self._json(200, payload)
            except Exception as e:
                self._json(502, {"error": "pull failed: %s" % e})
        elif path == "/api/log":
            try:
                log = load_log()
                weeks = sorted({e.get("week") for e in log})
                self._json(200, {"entries": log, "paper_weeks": len(weeks)})
            except Exception as e:
                self._json(500, {"error": "log read failed: %s" % e})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if urlparse(self.path).path != "/api/log":
            self._json(404, {"error": "not found"})
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n > 65536:
                self._json(413, {"error": "payload too large"})
                return
            entry = json.loads(self.rfile.read(n).decode("utf-8"))
            if not isinstance(entry, dict):
                raise ValueError("expected a JSON object")
            for field in ("date", "ticker", "iv_pct", "rv20_pct"):
                if field not in entry:
                    raise ValueError("missing field %s" % field)
            if entry.get("ticker", "").upper() not in ("UPRO", "TQQQ"):
                raise ValueError("ticker must be UPRO or TQQQ")
            datetime.strptime(str(entry.get("date", "")), "%Y-%m-%d")  # validate format
            entry["ticker"] = entry["ticker"].upper()
            log = append_log(entry)
            weeks = sorted({e.get("week") for e in log})
            self._json(200, {"ok": True, "entries": len(log), "paper_weeks": len(weeks)})
        except Exception as e:
            self._json(400, {"error": str(e)})

    def log_message(self, fmt, *args):
        sys.stderr.write("[NS-UPRO %s] %s\n" % (datetime.now().strftime("%H:%M:%S"), fmt % args))


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass  # suppress routine BrokenPipe from browser closes


if __name__ == "__main__":
    srv = QuietServer(("127.0.0.1", PORT), Handler)
    print("NS-UPRO calculator serving on http://127.0.0.1:%d" % PORT)
    srv.serve_forever()
