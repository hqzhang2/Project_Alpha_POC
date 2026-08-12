"""
scenario.py — Scenario engine orchestrator (add / remove / replace).

Thin orchestration layer: given a current portfolio and a proposed change,
compute the new target weights, generate funding paths via rebalance, and
compute drawdown impact via budget + enforcement.

Phase 1 is self-contained: current_weights + proposed change → target_weights
→ funding paths. External fetchers (A_T screener, NS-5 frontier, NS-2 regimes)
are OPTIONAL inputs; when absent, fail-open with conservative defaults.

Returns dicts matching the design-doc §5 API contract.
"""

import logging
from typing import Dict, List, Optional

import budget as budget_mod
import config
import enforcement as enforcement_mod
import rebalance as rebalance_mod

log = logging.getLogger("ns6.scenario")


def _target_after_change(current_weights, add=None, remove=None,
                         proposed_weight=None) -> Dict:
    """Compute target weights for a proposed add/remove/replace.

    For an ADD: add ticker at proposed_weight (or weighted among adds).
    For a REMOVE: drop ticker.
    For REPLACE: drop remove_ticker + add ticker at proposed_weight.
    Weights are NOT re-normalised here (PM accepts un-normalised funding maths;
    frontier re-run provides true targets in later phases).
    """
    target = dict(current_weights)
    if remove:
        target.pop(remove, None)
    if add:
        if proposed_weight is not None:
            target[add] = proposed_weight
        else:
            # fallback: keep existing weight if present, else 0.03 default
            target[add] = current_weights.get(add, 0.03)
    return target


def analyze_add(ticker, proposed_weight, current_weights, nav,
                prices=None, screener_scores=None, ns2_regimes=None,
                theta=None) -> Dict:
    theta = theta or config.load_theta()
    target = _target_after_change(current_weights, add=ticker,
                                  proposed_weight=proposed_weight)
    return _scenario_response("add", target, current_weights, nav, prices,
                              screener_scores, ns2_regimes, theta,
                              new_ticker=ticker)


def analyze_remove(ticker, current_weights, nav, prices=None,
                   screener_scores=None, ns2_regimes=None, theta=None) -> Dict:
    theta = theta or config.load_theta()
    target = _target_after_change(current_weights, remove=ticker)
    return _scenario_response("remove", target, current_weights, nav, prices,
                              screener_scores, ns2_regimes, theta,
                              removed_ticker=ticker)


def analyze_replace(remove_ticker, add_ticker, proposed_weight,
                    current_weights, nav, prices=None, screener_scores=None,
                    ns2_regimes=None, theta=None) -> Dict:
    theta = theta or config.load_theta()
    target = _target_after_change(current_weights, add=add_ticker,
                                  remove=remove_ticker,
                                  proposed_weight=proposed_weight)
    return _scenario_response("replace", target, current_weights, nav, prices,
                              screener_scores, ns2_regimes, theta,
                              new_ticker=add_ticker, removed_ticker=remove_ticker)


def _scenario_response(kind, target_weights, current_weights, nav, prices,
                       screener_scores, ns2_regimes, theta, new_ticker=None,
                       removed_ticker=None) -> Dict:
    prices = prices or {}
    screener_scores = screener_scores or {}
    ns2_regimes = ns2_regimes or {}

    funding_paths = rebalance_mod.generate_funding_paths(
        current_weights, target_weights, nav,
        screener_scores=screener_scores, ns2_regimes=ns2_regimes,
        theta=theta, prices=prices,
    )

    # Drawdown impact: show budget remaining (before) — computing "after" is
    # Phase 2 (requires frontier re-run). Phase 1: report budget consumption
    # of the largest new position's worst-case stop.
    budget_block = {"budget_before": None, "budget_after": None,
                    "worst_case_stop_cost": None}
    if new_ticker and new_ticker in target_weights and nav:
        w = target_weights[new_ticker]
        stop = theta["position_stops"].get("equity", theta["position_stops"]["unknown"])
        # worst-case cost = new weight × stop magnitude
        budget_block["worst_case_stop_cost"] = round(w * abs(stop), 4)

    return {
        "kind": kind,
        "new_ticker": new_ticker,
        "removed_ticker": removed_ticker,
        "target_weights": {k: round(v, 4) for k, v in target_weights.items()},
        "funding_paths": funding_paths,
        "drawdown_impact": budget_block,
        "screener": _screener_block(new_ticker, screener_scores),
    }


def _screener_block(ticker, screener_scores):
    if not ticker:
        return None
    return {"ticker": ticker,
            "agreement": screener_scores.get(ticker),
            "note": "screener verdict from A_T /api/screener (optional input)"}
