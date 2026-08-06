#!/usr/bin/env python3
"""
NS-5 Phase 1 unit tests — factor pipeline, regression engine, environment monitors.

Run with clean env (house rule):
  env -i HOME=$HOME /usr/bin/python3 -m pytest tests/ -q
or directly:
  env -i HOME=$HOME /usr/bin/python3 tests/test_phase1.py

No network access — all tests use synthetic data.
"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import environment  # noqa: E402
import regression  # noqa: E402
from data_fetcher import compute_log_returns  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic data helpers
# ---------------------------------------------------------------------------

def make_factor_frame(n=750, seed=42):
    """Synthetic 5-factor daily returns with known-ish correlations."""
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


def make_portfolio(factors, betas, alpha=0.0, seed=7):
    """Portfolio returns = alpha + betas @ factors + idiosyncratic noise."""
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, 0.01, len(factors))
    port = alpha + factors.to_numpy() @ np.array(betas) + noise
    return pd.Series(port, index=factors.index, name="portfolio")


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_factor_names_order(self):
        assert config.FACTOR_NAMES == ["MKT", "SMB", "HML", "MOM", "DUR"]

    def test_regressors_have_intercept_first(self):
        assert config.REGRESSORS[0] == "intercept"
        assert config.REGRESSORS[1:] == config.FACTOR_NAMES


# ---------------------------------------------------------------------------
# data_fetcher: log returns
# ---------------------------------------------------------------------------

class TestLogReturns:
    def test_returns_correct(self):
        closes = pd.DataFrame({
            "A": [100.0, 110.0, 121.0],
            "B": [50.0, 55.0, 49.5],
        }, index=pd.bdate_range("2024-01-01", periods=3))
        rets = compute_log_returns(closes)
        # warmup row (all-NaN) dropped: 3 bars -> 2 returns
        assert len(rets) == 2
        assert np.allclose(rets["A"].iloc[0], np.log(110 / 100))
        assert np.allclose(rets["A"].iloc[1], np.log(121 / 110))
        assert np.allclose(rets["B"].iloc[0], np.log(55 / 50))
        assert np.allclose(rets["B"].iloc[1], np.log(49.5 / 55))

    def test_non_positive_prices_masked(self):
        # A has a zero then negative-adjacent move; B stays valid so the row survives
        closes = pd.DataFrame({
            "A": [100.0, 0.0, 110.0],
            "B": [100.0, 105.0, 90.0],
        }, index=pd.bdate_range("2024-01-01", periods=3))
        rets = compute_log_returns(closes)
        # row 0 (warmup) dropped; row 1: A = log(0/100) = -inf -> NaN, B valid
        assert len(rets) == 2
        assert np.isnan(rets["A"].iloc[0])
        assert np.allclose(rets["B"].iloc[0], np.log(105 / 100))
        # row 2: A = log(110/0) = inf -> NaN, B valid
        assert np.isnan(rets["A"].iloc[1])
        assert np.allclose(rets["B"].iloc[1], np.log(90 / 105))
        # no inf/nan leakage into valid values
        assert np.isfinite(rets["B"]).all()

    def test_never_forward_fills(self):
        # NaN price in the middle: the return ACROSS the gap must NOT be computed
        # (would require forward-filling the NaN price)
        closes = pd.DataFrame({"A": [100.0, np.nan, 110.0]},
                              index=pd.bdate_range("2024-01-01", periods=3))
        rets = compute_log_returns(closes)
        # log(110/100) never appears — all rows NaN -> all-NaN rows dropped -> empty
        assert rets.empty


# ---------------------------------------------------------------------------
# regression engine
# ---------------------------------------------------------------------------

class TestRegression:
    def test_recovers_known_betas(self):
        factors = make_factor_frame()
        betas = [0.8, -0.2, 0.3, 0.4, 0.1]  # MKT, SMB, HML, MOM, DUR
        port = make_portfolio(factors, betas, alpha=0.0005)
        result = regression.regress(port, factors)
        assert result is not None
        for name, true_beta in zip(config.FACTOR_NAMES, betas):
            assert abs(result["beta"][name] - true_beta) < 0.03, \
                f"{name}: recovered {result['beta'][name]:.4f}, expected {true_beta}"
        assert abs(result["alpha"] - 0.0005) < 0.002

    def test_r_squared_high_with_little_noise(self):
        factors = make_factor_frame()
        port = make_portfolio(factors, [0.8, 0.1, 0.1, 0.1, 0.1], alpha=0.0, seed=1)
        result = regression.regress(port, factors)
        assert result["r_squared"] > 0.9

    def test_short_data_returns_none(self):
        factors = make_factor_frame(n=30)
        port = make_portfolio(factors, [1.0, 0, 0, 0, 0])
        assert regression.regress(port, factors) is None

    def test_nan_rows_dropped_not_fatal(self):
        factors = make_factor_frame()
        port = make_portfolio(factors, [0.5, 0, 0, 0, 0])
        port.iloc[10:20] = np.nan
        factors.iloc[30:35] = np.nan
        result = regression.regress(port, factors)
        assert result is not None
        assert abs(result["beta"]["MKT"] - 0.5) < 0.05

    def test_rolling_regress_shape(self):
        factors = make_factor_frame(n=750)
        port = make_portfolio(factors, [0.7, 0.1, 0.2, 0.1, 0.05])
        rolled = regression.rolling_regress(port, factors)
        assert not rolled.empty
        assert set(config.FACTOR_NAMES).issubset(rolled.columns)
        # ~750-250 = 500 obs / 21 step = ~23 windows (minus warmup drop)
        assert 10 < len(rolled) < 30
        assert abs(rolled["MKT"].mean() - 0.7) < 0.1


# ---------------------------------------------------------------------------
# environment monitors
# ---------------------------------------------------------------------------

class TestEnvironment:
    def test_rolling_vol_ddof1(self):
        rng = np.random.default_rng(0)
        s = pd.Series(rng.normal(0, 0.01, 500))
        vol = environment.rolling_vol(pd.DataFrame({"MKT": s}), 60)
        expected = s.rolling(60, min_periods=48).std(ddof=1) * np.sqrt(252)
        assert np.allclose(vol["MKT"].dropna(), expected.dropna(), rtol=1e-6)

    def test_vol_regime_flat_series_no_flag(self):
        rng = np.random.default_rng(0)
        # constant-vol synthetic: short/long ratio should hover near 1
        s = pd.Series(rng.normal(0, 0.01, 800))
        frame = pd.DataFrame({f: s for f in config.FACTOR_NAMES},
                             index=pd.bdate_range("2023-01-01", periods=800))
        ratio = environment.vol_regime_series(frame)
        last = ratio.iloc[-1]
        assert all(abs(v - 1.0) < 0.5 for v in last.dropna())  # no regime shift

    def test_rolling_corr_known(self):
        rng = np.random.default_rng(1)
        common = rng.normal(0, 1, 300)
        a = common + rng.normal(0, 0.1, 300)
        b = common + rng.normal(0, 0.1, 300)
        frame = pd.DataFrame({"MKT": a, "SMB": b}, index=pd.bdate_range("2023-01-01", periods=300))
        pairs = environment.rolling_corr(frame, 120)
        assert ("MKT", "SMB") in pairs
        corr_now = pairs[("MKT", "SMB")].dropna().iloc[-1]
        assert corr_now > 0.9

    def test_environment_summary_shape(self):
        factors = make_factor_frame()
        summary = environment.environment_summary(factors)
        assert "as_of" in summary
        assert set(config.FACTOR_NAMES).issubset(summary["vol_60d_ann"].keys())
        assert "flags" in summary
        assert "factor_vol_ratios" in summary["flags"]
        assert "corr_shifts" in summary["flags"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
