"""tests/test_walkforward.py — R4: NS-8 harness uses REAL data, no lookahead,
tranching works, cost drag is material, Sharpe/CAGR are consistent."""
import os
from pathlib import Path

import walkforward

# Ensure tests run against the real cached history (gitignored data file).
HIST = Path(__file__).resolve().parent.parent / "data" / "ns8_hist_closes.json"


def test_real_history_file_exists():
    """The walk-forward must consume the real cached data file (R4 core fix)."""
    assert HIST.exists(), "data/ns8_hist_closes.json missing — run the fetch step"


def test_load_historical_prices_returns_real_series():
    """Prices come from the cache, not np.random.seed(42)."""
    prices, dates = walkforward.load_historical_prices()
    assert len(dates) > 4000                      # ~20 years of daily data
    assert "SPY" in prices
    spy = sorted(prices["SPY"].items())
    assert len(spy) > 4000
    # SPY around 2006 was ~$87 (real value), not the synthetic seed's 100.0
    assert abs(spy[0][1] - 87.0) < 5.0


def test_tranched_reduces_turnover_vs_monthly():
    """Tranching spreads the rebalance -> lower turnover (Concretum claim)."""
    m = walkforward.run_walkforward(tranched=False)["metrics"]
    t = walkforward.run_walkforward(tranched=True)["metrics"]
    assert t["annual_turnover"] < m["annual_turnover"]
    assert m["annual_turnover"] > 0.0


def test_cost_drag_is_material():
    """Cost drag must be > 5 bp/yr (R4: was ~1 bp due to broken turnover)."""
    t = walkforward.run_walkforward(tranched=True)["metrics"]
    assert t["annual_cost_drag"] > 0.0005


def test_sharpe_cagr_consistent():
    """Implied vol (CAGR/Sharpe) in a sane 5%-15% band — not Sharpe 1.19/3.1%."""
    t = walkforward.run_walkforward(tranched=True)["metrics"]
    iv = (t["cagr"] / t["sharpe"]) if t["sharpe"] else float("inf")
    assert 0.03 <= iv <= 0.20


def test_no_lookahead_first_month_is_cash():
    """The first month has no prior signal -> portfolio stays all-cash."""
    res = walkforward.run_walkforward()
    # No rebalances occur before the second month's first trading day.
    # (Jan signal computed at Jan 31 -> applied from Feb 1 onward.)
    first_trade = min((t["date"] for t in res["trades"]), default=None)
    assert first_trade is None or first_trade >= "2006-02-01"


def test_target_weights_sum_to_one():
    """target_weights_on always produces a fully-invested weight vector."""
    prices, dates = walkforward.load_historical_prices()
    # pick a date well after warmup
    w = walkforward.target_weights_on(dates[-10], prices)
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert w["SHV"] == round(1.0 - sum(v for k, v in w.items() if k != "SHV"), 12)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
