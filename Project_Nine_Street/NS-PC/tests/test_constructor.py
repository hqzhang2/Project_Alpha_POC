"""tests/test_constructor.py — NS-PC portfolio constructor."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import constructor


def _sample_inputs():
    alloc = {"strategies": {"ns7": 0.2, "at_val": 0.2, "ns8": 0.4, "cash": 0.2}}
    blend = {"regime": "defensive", "blended": {"AAPL": 0.5, "MSFT": 0.5}}
    signals = {"weights": {"SPY": 0.2, "EFA": 0.2, "IEF": 0.0, "VNQ": 0.2,
                           "DBC": 0.2, "SHV": 0.2}}
    return alloc, blend, signals


def test_compose_sums_to_one():
    alloc, blend, signals = _sample_inputs()
    w = constructor.compose(alloc, blend, signals)
    assert abs(sum(w.values()) - 1.0) < 1e-6


def test_compose_long_only():
    alloc, blend, signals = _sample_inputs()
    w = constructor.compose(alloc, blend, signals)
    assert all(v >= 0 for v in w.values())


def test_equity_weight_is_sum_of_sleeves():
    # ns7 0.2 + at_val 0.2 = 0.4 equity allocation to the blended book
    alloc, blend, signals = _sample_inputs()
    w = constructor.compose(alloc, blend, signals)
    aapl = w.get("AAPL", 0.0) + w.get("MSFT", 0.0)
    assert abs(aapl - 0.4) < 1e-6


def test_cash_proxy_bil_present():
    alloc, blend, signals = _sample_inputs()
    w = constructor.compose(alloc, blend, signals)
    assert config.CASH_PROXY in w
    assert w[config.CASH_PROXY] > 0


def test_apply_guards_per_name_cap():
    # a single dominant name must be capped at 8%, excess → cash (BIL)
    w = {"AAA": 0.95, "BBB": 0.05}
    g = constructor.apply_guards(w)
    assert max(v for k, v in g.items() if k != config.CASH_PROXY) <= config.COMPOSED_MAX_NAME_W + 1e-6
    assert abs(sum(g.values()) - 1.0) < 1e-6
    # excess redistributed: cash proxy holds the capped-off weight
    assert g.get(config.CASH_PROXY, 0.0) > 0.5


def test_apply_guards_eff_n_reported_not_flattened():
    # extreme concentration → eff-N reported LOW (not silently flattened to equal weight)
    w = {"AAA": 1.0}
    g = constructor.apply_guards(w)
    gr = constructor.guardrails(g)
    assert "eff_n" in gr
    assert gr["eff_n"] < config.COMPOSED_MIN_EFF_N     # honest: concentration is flagged
    assert abs(g["AAA"] - config.COMPOSED_MAX_NAME_W) < 1e-6   # capped, not destroyed


def test_apply_guards_redistribute_not_reinflate():
    # capping AAA must NOT re-inflate AAA after renormalize
    w = {"AAA": 0.5, "BBB": 0.3, "CCC": 0.2}
    g = constructor.apply_guards(w)
    assert g["AAA"] <= config.COMPOSED_MAX_NAME_W + 1e-6
    assert abs(sum(g.values()) - 1.0) < 1e-6


def test_guardrails_block():
    g = constructor.guardrails({"A": 0.5, "B": 0.5})
    assert g["n"] == 2
    assert abs(g["eff_n"] - 2.0) < 1e-2
    assert abs(g["weights_sum"] - 1.0) < 1e-6


def test_effective_n_bounds():
    assert abs(constructor.effective_n({"A": 0.5, "B": 0.5}) - 2.0) < 1e-6


def test_materialize_whole_shares():
    w = {"AAPL": 1.0}
    prices = {"AAPL": 100.0}
    pos, cash = constructor.materialize(w, 100000.0, prices)
    assert pos["AAPL"]["shares"] == 1000            # floor(100000*1.0/100)
    assert cash == 0.0


def test_materialize_residual_cash():
    w = {"AAPL": 0.5, "MSFT": 0.5}
    prices = {"AAPL": 100.0, "MSFT": 300.0}
    pos, cash = constructor.materialize(w, 100000.0, prices)
    # 500 AAPL shares (50000) + 166 MSFT (49800) = 99800 → 200 residual
    assert pos["AAPL"]["shares"] == 500
    assert pos["MSFT"]["shares"] == 166
    assert abs(cash - 200.0) < 1.0


def test_materialize_skips_missing_price():
    w = {"AAPL": 0.5, "NOPRICE": 0.5}
    prices = {"AAPL": 100.0}
    pos, cash = constructor.materialize(w, 100000.0, prices)
    assert "NOPRICE" not in pos                    # fail-open: skip no-price


def test_build_portfolio_schema():
    alloc, blend, signals = _sample_inputs()
    prices = {"AAPL": 100.0, "MSFT": 200.0, "SPY": 400.0, "EFA": 70.0, "VNQ": 90.0,
              "DBC": 20.0, "BIL": 91.0}
    doc = constructor.build_portfolio(alloc, blend, signals, prices)
    assert "account" in doc and "positions" in doc and "history" in doc
    assert doc["account"]["last_updated"]
    assert len(doc["history"]) >= 1
    assert doc["positions"]["equities"]            # non-empty


def test_fail_open_missing_input():
    # stale/missing input → read_inputs returns None
    # (run_construct raises; tested indirectly via read_inputs None)
    import tempfile, os
    # monkeypatch a missing path
    old = config.NSX_ALLOC
    config.NSX_ALLOC = Path("/nonexistent/alloc.json")
    try:
        alloc, blend, signals = constructor.read_inputs()
        assert alloc is None
    finally:
        config.NSX_ALLOC = old


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
