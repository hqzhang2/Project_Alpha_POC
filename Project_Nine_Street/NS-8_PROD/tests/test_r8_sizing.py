"""tests/test_r8_sizing.py — R8: inverse-vol sizing + 12-month sign signal."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import signals
import vol


# ── vol.py ───────────────────────────────────────────────────────────────
def test_ewma_var_positive_and_stable():
    r = [0.01] * 200
    v = vol.exante_vol(r)
    assert v is not None and v >= 0
    # a higher-vol series -> higher ex-ante vol
    r2 = [0.02 if i % 2 else -0.02 for i in range(200)]
    assert vol.exante_vol(r2) > v


def test_exante_vol_none_on_insufficient_history():
    assert vol.exante_vol([]) is None
    assert vol.exante_vol([0.01, 0.01]) is None      # < 3 obs


# ── inverse-vol weights ──────────────────────────────────────────────────
def test_inverse_vol_sum_to_one():
    sigs = {"SPY": 1, "EFA": 1, "IEF": 0, "VNQ": 1, "DBC": 1}
    vols = {"SPY": 0.15, "EFA": 0.16, "VNQ": 0.20, "DBC": 0.35}
    w = signals.compute_weights_inverse_vol(sigs, vols)
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert w["IEF"] == 0.0
    assert w["DBC"] < w["SPY"]                 # higher vol -> lower weight
    # total risky = 0.20 × 4 in-trend = 0.80
    risky = sum(w[t] for t in ["SPY", "EFA", "VNQ", "DBC"])
    assert abs(risky - 0.80) < 1e-9
    assert abs(w["SHV"] - 0.20) < 1e-9


def test_inverse_vol_all_out_of_trend_goes_cash():
    sigs = {"SPY": 0, "EFA": 0, "IEF": 0, "VNQ": 0, "DBC": 0}
    vols = {"SPY": 0.15, "EFA": 0.16, "IEF": 0.05, "VNQ": 0.20, "DBC": 0.35}
    w = signals.compute_weights_inverse_vol(sigs, vols)
    assert w["SHV"] == 1.0


def test_inverse_vol_missing_vol_fails_open():
    sigs = {"SPY": 1, "EFA": 1}
    vols = {"SPY": 0.15}                       # EFA vol missing
    w = signals.compute_weights_inverse_vol(sigs, vols)
    assert w["EFA"] == 0.0
    assert abs(w["SPY"] - 0.20) < 1e-9         # only 1 in-trend with vol -> 20%
    assert abs(w["SHV"] - 0.80) < 1e-9


def test_inverse_vol_float_residue():
    sigs = {"SPY": 1, "EFA": 1, "IEF": 1, "VNQ": 1, "DBC": 1}
    vols = {"SPY": 0.15, "EFA": 0.16, "IEF": 0.05, "VNQ": 0.20, "DBC": 0.35}
    w = signals.compute_weights_inverse_vol(sigs, vols)
    assert w["SHV"] == round(1.0 - sum(v for k, v in w.items() if k != "SHV"), 12)


# ── 12-month sign signal ─────────────────────────────────────────────────
def test_sign12m_matches_on_trend():
    up = list(range(100, 352))                  # steadily rising -> long
    down = list(range(352, 100, -1))            # steadily falling -> cash
    assert signals.generate_signals_sign12m({"A": up})["A"] == 1
    assert signals.generate_signals_sign12m({"B": down})["B"] == 0


def test_sign12m_insufficient_history_cash():
    short = [100.0] * 100                        # < 252 days
    assert signals.generate_signals_sign12m({"C": short})["C"] == 0


# ── end-to-end: inverse-vol meets the MaxDD gate ─────────────────────────
def test_r8_inverse_vol_passes_maxdd_gate():
    """On real data, inverse-vol (R8 default) must keep MaxDD <= 15%."""
    import walkforward
    m = walkforward.run_walkforward(tranched=True)["metrics"]
    assert m["max_drawdown"] <= 0.15, f"MaxDD {m['max_drawdown']:.2%} > 15%"


def test_r8_beats_fixed_on_sharpe_and_dd():
    """Inverse-vol (R8) >= fixed (v1) on Sharpe and MaxDD — the R8 gate."""
    import walkforward
    saved = config.SIZING_METHOD
    try:
        config.SIZING_METHOD = "fixed"
        m_fixed = walkforward.run_walkforward(tranched=True)["metrics"]
        config.SIZING_METHOD = "inverse_vol"
        m_inv = walkforward.run_walkforward(tranched=True)["metrics"]
    finally:
        config.SIZING_METHOD = saved
    assert m_inv["sharpe"] >= m_fixed["sharpe"]
    assert m_inv["max_drawdown"] <= m_fixed["max_drawdown"]


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
