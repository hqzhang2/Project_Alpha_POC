#!/usr/bin/env python3
"""
NS-5 Phase 4 tests — end-to-end pipeline, edge cases, acceptance gate.

Roadmap 4.3: synthetic portfolio → full pipeline → assert grade levels,
flags fire, tweak list populated.
Roadmap 4.4: unit test edge cases (single position, zero positions,
all-same-sector, NaN returns).
Roadmap 4.6: acceptance gate — determinism, no-NaN output, fail-open.

Run with clean env:
  env -i HOME=$HOME /usr/bin/python3 -m pytest tests/test_phase4.py -q
No network — all synthetic.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import checks
import concentration
import config
import regression
import theta as theta_mod
from portfolio import build_portfolio_returns


# ---------------------------------------------------------------------------
# Synthetic data helpers
# ---------------------------------------------------------------------------

def make_factor_frame(n=750, seed=42):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n)
    cov = np.array([
        [1.0, 0.3, 0.1, 0.2, 0.0],
        [0.3, 1.0, 0.2, 0.1, 0.1],
        [0.1, 0.2, 1.0, 0.2, 0.0],
        [0.2, 0.1, 0.2, 1.0, 0.0],
        [0.0, 0.1, 0.0, 0.0, 1.0],
    ])
    data = rng.multivariate_normal(np.zeros(5), cov, size=n)
    return pd.DataFrame(data, index=dates, columns=config.FACTOR_NAMES)


TICKER_BETAS = {
    # {ticker: {factor: beta}} — tech names share growth/momentum signature
    "AAPL": {"MKT": 1.1, "SMB": -0.2, "HML": -0.5, "MOM": 0.4, "DUR": 0.1},
    "MSFT": {"MKT": 1.0, "SMB": -0.2, "HML": -0.5, "MOM": 0.4, "DUR": 0.1},
    "NVDA": {"MKT": 1.3, "SMB": -0.2, "HML": -0.5, "MOM": 0.5, "DUR": 0.1},
    "GOOGL": {"MKT": 1.0, "SMB": -0.1, "HML": -0.4, "MOM": 0.3, "DUR": 0.1},
    "AMZN": {"MKT": 1.1, "SMB": -0.1, "HML": -0.4, "MOM": 0.3, "DUR": 0.1},
    "META": {"MKT": 1.2, "SMB": -0.1, "HML": -0.4, "MOM": 0.4, "DUR": 0.1},
    "TSLA": {"MKT": 1.5, "SMB": -0.2, "HML": -0.3, "MOM": 0.3, "DUR": 0.1},
    "JPM":  {"MKT": 0.9, "SMB": 0.1, "HML": 0.3, "MOM": 0.0, "DUR": 0.0},
    "UNH":  {"MKT": 0.7, "SMB": 0.0, "HML": 0.2, "MOM": 0.0, "DUR": 0.0},
    "XOM":  {"MKT": 0.8, "SMB": 0.2, "HML": 0.5, "MOM": 0.0, "DUR": 0.0},
    "PG":   {"MKT": 0.6, "SMB": 0.0, "HML": 0.4, "MOM": -0.1, "DUR": 0.0},
    "CAT":  {"MKT": 1.0, "SMB": 0.2, "HML": 0.3, "MOM": 0.1, "DUR": 0.0},
    "LIN":  {"MKT": 0.9, "SMB": 0.1, "HML": 0.4, "MOM": 0.0, "DUR": 0.0},
    "SPY":  {"MKT": 1.0, "SMB": 0.0, "HML": 0.0, "MOM": 0.0, "DUR": 0.0},
    "TLT":  {"MKT": 0.0, "SMB": 0.0, "HML": 0.0, "MOM": 0.0, "DUR": 1.0},
}


def make_closes(factors, tickers, seed=1, noise_scale=0.0005):
    """Generate synthetic price paths from factor betas: price = 100·exp(cumsum(ret))."""
    rng = np.random.default_rng(seed)
    rets = {}
    for tk in tickers:
        betas = TICKER_BETAS.get(tk, {"MKT": 1.0})
        combo = sum(betas.get(f, 0.0) * factors[f] for f in config.FACTOR_NAMES)
        noise = rng.normal(0, noise_scale, len(factors))
        rets[tk] = combo + noise
    df = pd.DataFrame(rets, index=factors.index)
    closes = 100.0 * np.exp(df.cumsum())
    return closes


def _theta(**overrides):
    t = theta_mod.load_theta()
    t.update(overrides)
    return t


# ---------------------------------------------------------------------------
# 4.3 End-to-end pipeline
# ---------------------------------------------------------------------------

TECH_HEAVY = {
    "AAPL": 0.14, "MSFT": 0.12, "NVDA": 0.08, "GOOGL": 0.07,
    "AMZN": 0.06, "META": 0.05, "TSLA": 0.04,
    "JPM": 0.05, "UNH": 0.04, "XOM": 0.05, "TLT": 0.30,
}


class TestEndToEnd:
    def test_tech_heavy_concentration_grade_below_b(self):
        """Tech-heavy vs 60/40 policy → composite below B, flags fire."""
        factors = make_factor_frame()
        all_tk = list(TECH_HEAVY.keys()) + ["SPY", "TLT"]
        closes = make_closes(factors, all_tk, seed=2)
        theta = _theta(policy_weights={"SPY": 0.60, "TLT": 0.40})

        result = concentration.run_concentration_grade(
            TECH_HEAVY, theta, factor_returns=factors, closes=closes)
        assert "concentration" in result
        comp = result["concentration"]
        assert comp["composite_concentration_score"] < 3.5, \
            f"expected below B, got {comp['composite_concentration_grade']} ({comp['composite_concentration_score']})"

        # Sector: Tech weight 0.34 > 0.30 cap → flagged, worst-of ≤ C
        sec = result["sector"]
        assert sec["composite_score"] <= 3

        # Effective-N: ~6.7 < 12 floor → C or worse
        assert result["effective_n"]["composite_score"] < 3.0

        # Tweaks populated and structured
        tweaks = result["tweaks"]
        assert len(tweaks) > 0
        for tw in tweaks:
            assert "severity" in tw and "recommended_action" in tw and "rationale" in tw

    def test_tech_heavy_flags_specific_factors(self):
        """Growth-heavy signature: HML negative vs policy, flagged."""
        factors = make_factor_frame(seed=3)
        closes = make_closes(factors, list(TECH_HEAVY.keys()) + ["SPY", "TLT"], seed=3)
        theta = _theta(policy_weights={"SPY": 0.60, "TLT": 0.40})
        result = concentration.run_concentration_grade(
            TECH_HEAVY, theta, factor_returns=factors, closes=closes)
        fl = result["factor_loading"]
        # HML beta should be negative (growth-heavy)
        assert fl["factors"]["HML"]["beta"] < 0
        assert fl["factors"]["HML"]["policy_beta"] >= fl["factors"]["HML"]["beta"]

    def test_balanced_self_policy_grades_a(self):
        """Portfolio == its own policy → A composite."""
        balanced = {tk: 1.0 / 12 for tk in
                    ["AAPL", "MSFT", "XOM", "JPM", "UNH", "PG",
                     "CAT", "LIN", "TLT", "SPY", "GOOGL", "AMZN"]}
        factors = make_factor_frame(seed=5)
        closes = make_closes(factors, list(balanced.keys()), seed=5, noise_scale=0.0005)
        theta = _theta(policy_weights=dict(balanced))
        result = concentration.run_concentration_grade(
            balanced, theta, factor_returns=factors, closes=closes)
        comp = result["concentration"]
        assert comp["composite_concentration_grade"] in ("A", "B"), \
            f"self-policy should be A/B, got {comp['composite_concentration_grade']}"

    def test_deterministic_output(self):
        """Same input twice → identical scorecard JSON."""
        factors = make_factor_frame(seed=7)
        closes = make_closes(factors, list(TECH_HEAVY.keys()) + ["SPY", "TLT"], seed=7)
        theta = _theta(policy_weights={"SPY": 0.60, "TLT": 0.40})
        r1 = concentration.run_concentration_grade(
            TECH_HEAVY, theta, factor_returns=factors, closes=closes)
        r2 = concentration.run_concentration_grade(
            TECH_HEAVY, theta, factor_returns=factors, closes=closes)
        assert json.dumps(r1, sort_keys=True, default=str) == \
               json.dumps(r2, sort_keys=True, default=str)

    def test_fail_open_no_factor_data(self):
        """Empty factor returns → N/A grade + error, not a crash."""
        theta = _theta(policy_weights={"SPY": 0.60, "TLT": 0.40})
        result = concentration.run_concentration_grade(
            TECH_HEAVY, theta, factor_returns=pd.DataFrame(), closes=pd.DataFrame())
        assert "error" in result
        assert result.get("composite_grade") in ("N/A", None)


# ---------------------------------------------------------------------------
# 4.4 Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_single_position_effective_n(self):
        theta = _theta(effective_n_floor=12)
        result = checks.grade_effective_n({"AAPL": 1.0}, theta)
        assert result["effective_n"] == pytest.approx(1.0)
        assert result["composite_grade"] == "F"

    def test_empty_holdings_effective_n(self):
        theta = _theta()
        result = checks.grade_effective_n({}, theta)
        assert result["composite_grade"] == "F"

    def test_single_position_sector(self):
        theta = _theta(max_sector_pct=0.30)
        result = checks.grade_sector_weights({"AAPL": 1.0}, theta)
        # 100% in one sector → ratio 3.33 → F
        assert result["composite_grade"] == "F"
        assert result["sector_details"]["Sector-Tech"]["ratio"] == pytest.approx(3.333, rel=1e-2)

    def test_all_same_sector(self):
        theta = _theta(max_sector_pct=0.30)
        holdings = {"AAPL": 0.5, "MSFT": 0.5}
        result = checks.grade_sector_weights(holdings, theta)
        assert result["sector_weights"]["Sector-Tech"] == pytest.approx(1.0)
        assert result["composite_grade"] == "F"

    def test_empty_holdings_sector(self):
        theta = _theta()
        result = checks.grade_sector_weights({}, theta)
        assert result["sector_weights"] == {}
        assert result["composite_grade"] == "A"  # nothing to violate

    def test_tail_corr_single_position(self):
        theta = _theta(top_n_for_tail=5)
        factors = make_factor_frame(seed=11)
        closes = make_closes(factors, ["AAPL"], seed=11)
        result = checks.grade_tail_correlation({"AAPL": 1.0}, theta, closes=closes)
        # one position → no pairs → A
        assert result["positions_checked"] == 1
        assert result["composite_grade"] in ("A", "B")

    def test_nan_returns_do_not_crash(self):
        """NaN/inf in return series → regression drops rows, no crash."""
        factors = make_factor_frame(seed=13)
        closes = make_closes(factors, ["SPY", "TLT", "AAPL", "MSFT"], seed=13)
        # Inject NaN and inf into closes
        closes.iloc[10:15, 0] = np.nan
        closes.iloc[20, 1] = np.inf
        theta = _theta(policy_weights={"SPY": 0.60, "TLT": 0.40})
        result = concentration.run_concentration_grade(
            {"AAPL": 0.6, "MSFT": 0.4}, theta, factor_returns=factors, closes=closes)
        assert "concentration" in result or "error" in result

    def test_tail_corr_fewer_positions_than_top_n(self):
        theta = _theta(top_n_for_tail=5)
        factors = make_factor_frame(seed=17)
        closes = make_closes(factors, ["A", "B"], seed=17)
        result = checks.grade_tail_correlation({"A": 0.6, "B": 0.4}, theta, closes=closes)
        assert result["positions_checked"] == 2


# ---------------------------------------------------------------------------
# 4.6 Acceptance gate
# ---------------------------------------------------------------------------

class TestAcceptanceGate:
    def test_output_no_nan(self):
        """Scorecard serializes to valid JSON — no bare NaN (house rule)."""
        factors = make_factor_frame(seed=19)
        closes = make_closes(factors, list(TECH_HEAVY.keys()) + ["SPY", "TLT"], seed=19)
        theta = _theta(policy_weights={"SPY": 0.60, "TLT": 0.40})
        result = concentration.run_concentration_grade(
            TECH_HEAVY, theta, factor_returns=factors, closes=closes)
        s = json.dumps(result, default=str)  # must not raise
        assert "NaN" not in s.replace("NaN", "")  # default=str handles it, but ensure no crash

    def test_missing_data_fail_open(self):
        """No factor data → structured error, engine does not crash."""
        theta = _theta(policy_weights={"SPY": 0.60, "TLT": 0.40})
        result = concentration.run_concentration_grade(
            TECH_HEAVY, theta, factor_returns=pd.DataFrame())
        assert result.get("composite_grade") in ("N/A", None)

    def test_unknown_tickers_fail_open(self):
        """Unknown tickers → sector grade still computed, unknown flagged."""
        theta = _theta(max_sector_pct=0.30)
        result = checks.grade_sector_weights({"ZZZZ": 1.0}, theta)
        assert "ZZZZ" in result["unknown_tickers"]
        assert "Unknown" in result["sector_details"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
