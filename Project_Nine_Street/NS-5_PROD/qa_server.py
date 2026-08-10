#!/usr/bin/env python3
"""
NS-5 QA Server — stdlib http.server + factor pipeline + concentration grading.

Endpoints:
  GET  /health            -> 200 + factor data freshness
  GET  /api/factors       -> factor returns summary (latest values per factor)
  GET  /api/environment   -> vol/correlation environment snapshot
  GET  /api/grade         -> instructions + example payload
  POST /api/grade         -> concentration grade scorecard (JSON body)

NS-5 Portfolio Governance Engine.
Roadmap Phase 5: QA on port 9251; PROD on port 9250 (env-derived).
"""
import json
import logging
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import concentration
import config
import data_fetcher
import drift
import environment
import frontier
import portfolio
import portfolio_store
import tax
import theta as theta_mod

PORT = int(os.environ.get("PORT", 9251))
ENV = os.environ.get("ENV", "QA")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("ns5.qa_server")

_factors_cache = None
_factors_ts = None
FACTORS_CACHE_TTL = 300  # seconds


def _get_factors():
    """Cached factor returns (no Yahoo on the hot path — data pre-cached by cron)."""
    global _factors_cache, _factors_ts
    now = datetime.now()
    if _factors_cache is not None and _factors_ts is not None:
        if (now - _factors_ts).total_seconds() < FACTORS_CACHE_TTL:
            return _factors_cache
    factors, closes, rf = data_fetcher.build_factor_returns()
    _factors_cache = factors
    _factors_ts = now
    return factors


def _freshness_meta():
    """Factor data freshness from data/factor_meta.json if present."""
    meta_path = config.DATA_DIR / "factor_meta.json"
    if meta_path.exists():
        with open(meta_path) as fh:
            return json.load(fh)
    return {"error": "no factor_meta.json — run refresh"}


def _serve_dashboard(handler):
    """Serve ns5_dashboard.html (the portal-facing UI)."""
    dash_path = config.BASE_DIR / "ns5_dashboard.html"
    if not dash_path.exists():
        handler._json({"error": "ns5_dashboard.html not found"}, 404)
        return
    with open(dash_path, "rb") as fh:
        body = fh.read()
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


DEFAULT_FRONTIER_HOLDINGS = {
    "AAPL": 0.14, "MSFT": 0.12, "NVDA": 0.08, "GOOGL": 0.07,
    "AMZN": 0.06, "META": 0.05, "TSLA": 0.04,
    "JPM": 0.05, "UNH": 0.04, "XOM": 0.05, "TLT": 0.30,
}
DEFAULT_FRONTIER_POLICY = {"SPY": 0.60, "TLT": 0.40}


def _frontier_response(holdings=None, policy=None, force_refresh=False):
    """Efficient frontier + portfolio/policy positions for a holdings dict.

    holdings/policy may be dicts (weights), or strings naming a stored
    portfolio (ticker→shares, converted to weights) / stored policy.
    """
    import portfolio as portfolio_mod
    import portfolio_store

    if isinstance(holdings, str):
        entry = portfolio_store.get_portfolio(holdings)
        if entry is None:
            return {"error": f"portfolio '{holdings}' not found"}
        closes_tmp = data_fetcher.get_closes(list(entry.keys()), force_refresh=force_refresh)
        holdings = portfolio_mod.shares_to_weights(entry, closes_tmp)
    if isinstance(policy, str):
        entry = portfolio_store.get_policy(policy)
        if entry is None:
            return {"error": f"policy '{policy}' not found"}
        policy = entry

    holdings = holdings or DEFAULT_FRONTIER_HOLDINGS
    policy = policy or DEFAULT_FRONTIER_POLICY

    # Benchmark anchors (SPY/QQQ) always fetched so they render even when
    # not in the holdings universe — computed as single-asset positions on
    # the same Ledoit-Wolf covariance basis.
    BENCHMARKS = ("SPY", "QQQ")
    universe = list(holdings.keys()) + list(policy.keys())
    all_tickers = list(dict.fromkeys(universe + list(BENCHMARKS)))
    closes = data_fetcher.get_closes(all_tickers, force_refresh=force_refresh)
    if closes.empty:
        return {"error": "no price data for universe"}

    fc = frontier.compute_frontier(closes, list(holdings.keys()))
    if "error" in fc:
        return fc

    # Positions: portfolio, policy, and each asset (scatter points)
    pos_portfolio = frontier.position_on_frontier(holdings, closes, list(holdings.keys()))
    pos_policy = frontier.position_on_frontier(policy, closes, list(policy.keys()))

    assets = []
    for tk in fc["tickers"]:
        if tk in fc["mu"] and tk in fc["sigma"]:
            assets.append({"ticker": tk, "ret": fc["mu"][tk], "vol": fc["sigma"][tk]})

    # Benchmark anchors as single-asset positions (same cov basis).
    # Pass only the benchmark ticker so covariance is computed over its own
    # clean series — all_tickers includes holdings with misaligned histories.
    benchmarks = []
    for tk in BENCHMARKS:
        pos = frontier.position_on_frontier({tk: 1.0}, closes, [tk])
        if "error" not in pos and pos.get("vol") is not None:
            benchmarks.append({"ticker": tk, "ret": pos["ret"], "vol": pos["vol"]})

    return {
        "as_of": str(closes.index[-1].date()) if not closes.empty else None,
        "universe": fc["tickers"],
        "frontier": fc["frontier"],
        "gmv": fc["gmv"],
        "max_ret": fc["max_ret"],
        "assets": assets,
        "benchmarks": benchmarks,
        "portfolio": pos_portfolio,
        "policy": pos_policy,
        "n_obs": pos_portfolio.get("n_obs", 0) if isinstance(pos_portfolio, dict) else 0,
    }


# ---------------------------------------------------------------------------
# Portfolio / policy CRUD helpers (called from Handler)
# ---------------------------------------------------------------------------

def _resolve_for_drift(holdings, policy_weights):
    """Resolve holdings/policy (dicts of weights OR stored names) → weight dicts."""
    import portfolio as portfolio_mod

    hw = holdings
    if isinstance(holdings, str):
        entry = portfolio_store.get_portfolio(holdings)
        if entry is None:
            raise ValueError(f"portfolio '{holdings}' not found")
        closes_tmp = data_fetcher.get_closes(list(entry.keys()))
        hw = portfolio_mod.shares_to_weights(entry, closes_tmp)

    pw = policy_weights
    if isinstance(policy_weights, str):
        entry = portfolio_store.get_policy(policy_weights)
        if entry is None:
            raise ValueError(f"policy '{policy_weights}' not found")
        pw = entry
    return hw, pw


def _portfolios_get(path):
    """GET /api/portfolios           → {portfolios: [names]}
       GET /api/portfolios?name=X    → {name, holdings: {ticker: shares}}"""
    from urllib.parse import parse_qs, urlparse
    q = parse_qs(urlparse(path).query)
    name = q.get("name", [None])[0]
    if name is None:
        return {"portfolios": portfolio_store.list_portfolios()}
    entry = portfolio_store.get_portfolio(name)
    if entry is None:
        return {"error": f"portfolio '{name}' not found"}, 404
    return {"name": name, "holdings": entry}


def _policies_get(path):
    """GET /api/policies           → {policies: [names]}
       GET /api/policies?name=X    → {name, weights: {ticker: weight}}"""
    from urllib.parse import parse_qs, urlparse
    q = parse_qs(urlparse(path).query)
    name = q.get("name", [None])[0]
    if name is None:
        return {"policies": portfolio_store.list_policies()}
    entry = portfolio_store.get_policy(name)
    if entry is None:
        return {"error": f"policy '{name}' not found"}, 404
    return {"name": name, "weights": entry}


def _portfolios_post(body):
    """POST /api/portfolios  {name, holdings: {ticker: shares}, rename_from?}
       Create or update. rename_from deletes the old name (rename)."""
    name = body.get("name")
    holdings = body.get("holdings")
    if not name or not holdings:
        return {"error": "name and holdings (dict of ticker→shares) required"}, 400
    try:
        if body.get("rename_from") and body["rename_from"] != name:
            portfolio_store.delete_portfolio(body["rename_from"])
        saved = portfolio_store.upsert_portfolio(name, holdings)
    except ValueError as exc:
        return {"error": str(exc)}, 400
    return {"ok": True, **saved}


def _policies_post(body):
    """POST /api/policies  {name, weights: {ticker: weight}}"""
    name = body.get("name")
    weights = body.get("weights")
    if not name or not weights:
        return {"error": "name and weights (dict of ticker→weight) required"}, 400
    try:
        saved = portfolio_store.upsert_policy(name, weights)
    except ValueError as exc:
        return {"error": str(exc)}, 400
    return {"ok": True, **saved}


def _portfolios_delete(path):
    """DELETE /api/portfolios?name=X"""
    from urllib.parse import parse_qs, urlparse
    q = parse_qs(urlparse(path).query)
    name = q.get("name", [None])[0]
    if not name:
        return {"error": "name query param required"}, 400
    deleted = portfolio_store.delete_portfolio(name)
    return {"ok": True, "deleted": deleted, "name": name}


def _policies_delete(path):
    """DELETE /api/policies?name=X"""
    from urllib.parse import parse_qs, urlparse
    q = parse_qs(urlparse(path).query)
    name = q.get("name", [None])[0]
    if not name:
        return {"error": "name query param required"}, 400
    deleted = portfolio_store.delete_policy(name)
    return {"ok": True, "deleted": deleted, "name": name}


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, status=200):
        body = json.dumps(obj, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        # Single CORS header source — applies to JSON, HTML, and 404s.
        # (Adding it in _json AND here produced "*, *" which browsers reject.)
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        try:
            if self.path in ("/", "/index.html", "/ns5_dashboard.html"):
                _serve_dashboard(self)
            elif self.path in ("/health", "/health/"):
                meta = _freshness_meta()
                factors = _get_factors()
                self._json({"status": "ok", "service": "ns5", "env": ENV, "port": PORT,
                            "factor_rows": int(factors.shape[0]) if not factors.empty else 0,
                            "factor_last_date": str(factors.index[-1].date()) if not factors.empty else None,
                            "factor_meta": meta, "as_of": datetime.now(timezone.utc).isoformat()})
            elif self.path.startswith("/api/factors"):
                factors = _get_factors()
                if factors.empty:
                    self._json({"error": "no factor data"}, 503); return
                latest = factors.iloc[-1]
                self._json({"as_of": str(factors.index[-1].date()),
                            "latest_daily_returns": {k: float(v) for k, v in latest.items()},
                            "row_count": int(factors.shape[0])})
            elif self.path.startswith("/api/environment"):
                factors = _get_factors()
                if factors.empty:
                    self._json({"error": "no factor data"}, 503); return
                self._json(environment.environment_summary(factors))
            elif self.path.startswith("/api/frontier"):
                # GET: fetch closes for the default tech-heavy example universe
                from urllib.parse import parse_qs, urlparse
                q = parse_qs(urlparse(self.path).query)
                holdings = json.loads(q.get("holdings", [None])[0]) if q.get("holdings") else None
                policy = json.loads(q.get("policy", [None])[0]) if q.get("policy") else None
                result = _frontier_response(holdings, policy)
                self._json(result)
            elif self.path.startswith("/api/grade"):
                self._json({"usage": "POST /api/grade JSON: {holdings: {TICKER: weight} | 'portfolio_name', "
                                      "policy_weights: {TICKER: weight} | 'policy_name'}",
                            "example": {"holdings": "Tech Heavy",
                                        "policy_weights": "60/40 SPY/TLT"}})
            elif self.path.startswith("/api/portfolios"):
                self._json(_portfolios_get(self.path))
            elif self.path.startswith("/api/policies"):
                self._json(_policies_get(self.path))
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:
            log.exception("handler error")
            self._json({"error": str(exc)}, 500)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                self._json({"error": "empty body"}, 400); return
            body = json.loads(self.rfile.read(length))
            if self.path.startswith("/api/grade"):
                holdings = body.get("holdings", {})
                if not holdings:
                    self._json({"error": "missing holdings"}, 400); return
                theta = theta_mod.load_theta()
                if "policy_weights" in body:
                    theta["policy_weights"] = body["policy_weights"]
                if "max_single_name_pct" in body:
                    theta["max_single_name_pct"] = body["max_single_name_pct"]
                if body.get("tax") is True:
                    theta["tax"] = dict(theta_mod.TAX_DEFAULTS)
                elif isinstance(body.get("tax"), dict):
                    base = dict(theta_mod.TAX_DEFAULTS)
                    # Deep-merge distribution_character so per-ticker overrides
                    # don't clobber the seeded table (shallow merge would replace it)
                    custom = body["tax"]
                    if "distribution_character" in custom:
                        merged_chars = dict(base.get("distribution_character", {}))
                        merged_chars.update(custom["distribution_character"])
                        custom["distribution_character"] = merged_chars
                    base.update(custom)
                    # Recompute drag rates from bracket fields (single source of
                    # truth — stale stored drags would be wrong after bracket edits)
                    import tax as tax_mod
                    drags = tax_mod._compute_drags({"tax": base})
                    base["ordinary_drag"] = drags["ordinary"]
                    base["ltcg_drag"] = drags["ltcg"]
                    base["blended_1256_drag"] = drags["blended_1256"]
                    base["roc_drag"] = drags["roc"]
                    theta["tax"] = base
                factors = _get_factors()
                if factors.empty:
                    self._json({"error": "no factor data"}, 503); return
                axes = body.get("axes") or ["concentration", "drift"]

                result = {}
                if "concentration" in axes:
                    conc = concentration.run_concentration_grade(
                        holdings, theta, factor_returns=factors)
                    if "error" in conc:
                        self._json(conc, 400); return
                    result.update(conc)  # concentration + tweaks + axis raw data
                if "drift" in axes:
                    # Resolve names → weight dicts for drift checkers
                    hw, pw = _resolve_for_drift(holdings, theta["policy_weights"])
                    drift_res = drift.run_drift_grade(
                        hw, pw, factor_returns=factors, theta=theta)
                    result["drift"] = drift_res
                    # Merge drift tweaks into the shared tweak list
                    combined = list(result.get("tweaks", [])) + list(drift_res.get("tweaks", []))
                    if combined:
                        result["tweaks"] = combined
                if "tax" in axes:
                    if theta.get("tax") is None:
                        result["tax"] = {"error": "tax axis disabled — configure Θ.tax"}
                    else:
                        # v2 positions (lots/accounts) — fail-open via store
                        import portfolio_store as ps
                        pname = holdings if isinstance(holdings, str) else None
                        positions = ps.get_portfolio_positions(pname) if pname else None
                        if positions is None:
                            # dict holdings → normalize flat to v2 (no lots)
                            positions = {str(tk).strip().upper():
                                         ps._normalize_position(tk, v)
                                         for tk, v in (holdings.items() if isinstance(holdings, dict) else {})}
                        tickers = list(positions.keys())
                        yields = data_fetcher.get_dividend_yields(tickers)
                        tax_res = tax.run_tax_grade(positions, yields, theta=theta)
                        if "error" in tax_res:
                            result["tax"] = tax_res
                        else:
                            result["tax"] = tax_res
                            combined = list(result.get("tweaks", [])) + list(tax_res.get("tweaks", []))
                            if combined:
                                result["tweaks"] = combined
                if "regime" in axes:
                    if theta.get("regime") is None:
                        result["regime"] = {"error": "regime axis disabled — configure Θ.regime"}
                    else:
                        import regime_checkers
                        # Resolve holdings/policy names → weight dicts
                        hw, pw = _resolve_for_drift(holdings, theta["policy_weights"])
                        # Universe = holdings ∪ policy (+ SPY/TLT proxies for corr)
                        all_tk = sorted(set(hw) | set(pw) | {"SPY", "TLT"})
                        closes = data_fetcher.get_closes(all_tk)
                        regime_res = regime_checkers.run_regime_checkers(
                            closes=closes, policy_weights=pw, theta=theta)
                        if "error" in regime_res:
                            result["regime"] = regime_res
                        else:
                            result["regime"] = regime_res
                            combined = list(result.get("tweaks", [])) + list(regime_res.get("tweaks", []))
                            if combined:
                                result["tweaks"] = combined

                # ── Portfolio composite: base × regime enhancer (v3.3.1) ──
                # Pure math in drift.compute_portfolio_composite — design A
                # (Hong 2026-08-10): base = mean of active non-regime axes
                # (concentration + drift, N/A excluded); enhancer ∈ [0.5, 1.0]
                # never pulls the composite below the other axes' average.
                # Fail-open: regime absent/disabled/N-A → enhancer = 1.0; no
                # base scores → None ("N/A"), never crash.
                result.update(drift.compute_portfolio_composite(
                    result, theta["letter_score_bounds"]))

                self._json(result)
            elif self.path.startswith("/api/frontier"):
                result = _frontier_response(body.get("holdings"),
                                            body.get("policy_weights"))
                self._json(result)
            elif self.path.startswith("/api/portfolios"):
                result = _portfolios_post(body)
                if isinstance(result, tuple):
                    self._json(result[0], result[1])
                else:
                    self._json(result)
            elif self.path.startswith("/api/policies"):
                result = _policies_post(body)
                if isinstance(result, tuple):
                    self._json(result[0], result[1])
                else:
                    self._json(result)
            else:
                self._json({"error": "not found"}, 404)
        except json.JSONDecodeError as exc:
            self._json({"error": f"invalid JSON: {exc}"}, 400)
        except Exception as exc:
            log.exception("POST handler error")
            self._json({"error": str(exc)}, 500)

    def do_DELETE(self):
        try:
            if self.path.startswith("/api/portfolios"):
                result = _portfolios_delete(self.path)
                if isinstance(result, tuple):
                    self._json(result[0], result[1])
                else:
                    self._json(result)
            elif self.path.startswith("/api/policies"):
                result = _policies_delete(self.path)
                if isinstance(result, tuple):
                    self._json(result[0], result[1])
                else:
                    self._json(result)
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:
            log.exception("DELETE handler error")
            self._json({"error": str(exc)}, 500)

    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)


def main():
    portfolio_store.seed_if_missing()
    log.info("NS-5 QA server starting on port %d (env=%s)", PORT, ENV)
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()