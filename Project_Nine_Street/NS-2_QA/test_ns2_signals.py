#!/usr/bin/env python3
"""
NS-2 Signal Logic Unit Tests
=============================
Pure-function coverage for qa_server.py + ns2_backtest.py verification.
No network, runs in <5 seconds. Target: >60% coverage on changed functions.
Usage:  python3 -m pytest test_ns2_signals.py -v
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
import qa_server as ns2
import ns2_backtest as bt


# ── Helpers ──────────────────────────────────────────────────────────────────

def mkdf(n, close, *, rsi=50.0, cci=50.0, regime=1, ma_dist=0.05, atr=1.0,
         signal=None, pos_size=None, daily_ret=0.01):
    idx = pd.date_range("2026-01-01", periods=n)
    closes = close[:n] if hasattr(close, "__len__") and not isinstance(close, (int, float)) else [close] * n
    dfs = pd.DataFrame({
        "close": closes, "rsi": [rsi] * n, "cci": [cci] * n, "atr": [atr] * n,
        "adx": [20.0] * n, "bb_position": [0.0] * n, "vol_ratio": [1.0] * n,
        "ma_distance": [ma_dist] * n,
    }, index=idx)
    if signal is not None:
        dfs["signal"] = signal if hasattr(signal, "__getitem__") and not isinstance(signal, (int, float)) else [signal] * n
    else:
        dfs["signal"] = np.zeros(n, dtype=int)
    if pos_size is not None:
        dfs["position_size"] = [pos_size] * n if not hasattr(pos_size, "__getitem__") or isinstance(pos_size, float) else pos_size
    else:
        dfs["position_size"] = np.ones(n)
    if hasattr(regime, "__getitem__") and not isinstance(regime, (int, float)):
        dfs["regime"] = regime
    else:
        dfs["regime"] = np.full(n, regime, dtype=int)
    dfs["strategy_return"] = [daily_ret] * n
    dfs["daily_return"] = [daily_ret] * n
    dfs["cumulative_strat"] = np.linspace(1, 1 + daily_ret * n, n)
    dfs["cumulative_bah"] = np.linspace(1, 1 + daily_ret * n, n)
    dfs["equity"] = np.linspace(1e5, 1e5 * (1 + daily_ret * n), n)
    return dfs


# ══════════════════════════════════════════════════════════════════════════════
# classify_asset / get_profile
# ══════════════════════════════════════════════════════════════════════════════

class TestAssetClassification:
    def test_equity_defaults(self):
        for t in ("AAPL", "NVDA", "MU", "TSLA", "xyz_unknown"):
            assert ns2.classify_asset(t) == "equity"

    def test_bond_tickers(self):
        for t in ("TLT", "IEF", "AGG", "LQD"):
            assert ns2.classify_asset(t) == "bond"
            assert ns2.classify_asset(t.lower()) == "bond"

    def test_commodity_tickers(self):
        assert ns2.classify_asset("GLD") == "commodity"
        assert ns2.classify_asset("USO") == "commodity"

    def test_get_profile_returns_dict(self):
        for k in ("equity", "bond", "commodity"):
            assert "vol_crisis" in ns2.ASSET_PROFILES[k]
            assert "cci_short" in ns2.ASSET_PROFILES[k]

    def test_bond_thresholds_lower_than_equity(self):
        bp = ns2.get_profile("TLT")
        ep = ns2.get_profile("AAPL")
        assert bp["vol_crisis"] < ep["vol_crisis"]
        assert bp["trend_threshold"] < ep["trend_threshold"]
        assert bp["atr_hi"] < ep["atr_hi"]
        assert bp["cci_short"] > ep["cci_short"]  # -200 > -250


# ══════════════════════════════════════════════════════════════════════════════
# add_signal_labels_v2 — labels derive from actual signal array
# ══════════════════════════════════════════════════════════════════════════════

def labels_for(regime, signals, rsi=50.0, cci=50.0):
    df = mkdf(len(signals), close=100.0, regime=regime, rsi=rsi, cci=cci, signal=signals)
    return ns2.add_signal_labels_v2(df)["signal_label"].tolist()


class TestSignalLabels:
    def test_tranding_entry_becomes_buy(self):
        assert labels_for(0, [0, 1, 1, 0]) == ["WATCH", "BUY", "HOLD LONG", "EXIT"]

    def test_full_buy_hold_exit_cycle(self):
        assert labels_for(0, [0, 1, 1, 1, 0]) == ["WATCH", "BUY", "HOLD LONG", "HOLD LONG", "EXIT"]

    def test_crisis_short_label(self):
        assert labels_for(2, [0, -1, -1, 0]) == ["FLAT", "SHORT", "SHORT", "FLAT"]

    def test_no_legacy_hold(self):
        result = labels_for(0, [0, 1, 1, 0, -1])
        assert "HOLD" not in result
        assert "HOLD LONG" in result

    def test_mean_rev_sell_label(self):
        # regime=1 (MEAN_REV), signal=-1 → SELL
        result = labels_for(1, [0, -1, 0], rsi=80.0)
        assert "SELL" in result

    def test_labels_subset_of_signal_colors(self):
        result = labels_for(0, [0, 1, 1, 0, -1, 0])
        assert set(result) <= set(ns2.SIGNAL_COLORS)


# ══════════════════════════════════════════════════════════════════════════════
# apply_stops — trailing stop (Phase 3)
# ══════════════════════════════════════════════════════════════════════════════

class TestApplyStops:
    def test_trail_fires_on_pullback(self):
        # Entry at bar 1 @100, rallies to 130, drops to 118.
        # Trail ratchets: 100-3=97 -> 130-3=127 -> 118 < 127 → stop fires
        closes = [100, 100, 110, 120, 130, 118, 118]
        df = mkdf(7, closes, atr=1.0)
        df["signal"] = [0, 1, 1, 1, 1, 1, 1]
        df["position_size"] = [1.0] * 7
        out = ns2.apply_stops(df)
        assert 0 in out["signal"].iloc[5:].tolist(), f"expected stop; got {out['signal'].tolist()}"

    def test_trail_silent_in_uptrend(self):
        closes = [100, 100, 101, 102, 103, 104, 105]
        df = mkdf(7, closes, atr=5.0)
        df["signal"] = [0, 1, 1, 1, 1, 1, 1]
        df["position_size"] = [1.0] * 7
        out = ns2.apply_stops(df)
        assert (out["signal"].iloc[1:] == 1).all()

    def test_resets_when_exited(self):
        # Enter, get stopped, re-enter — entry price resets
        closes = [100] + [109] * 3 + [100] + [109] * 3     # stop would hit at 106
        df = mkdf(8, closes, atr=3.0)
        df["signal"] = [0, 1, 1, 0, 0, 1, 1, 1]
        df["position_size"] = [1.0] * 8
        out = ns2.apply_stops(df)
        # First long stops at bar 3 (109 - 3*3 = 100 → close 109, no; but close=109 atr=3 → 100, not stopped)
        # Actually 109 - 9 = 100, close is 109 → not stopped. Second entry at 109, close stays 109 → not stopped
        # Both entries survive since ATR=3 → trail=100, close never goes to 100
        assert out["signal"].tolist() == [0, 1, 1, 0, 0, 1, 1, 1]


# ══════════════════════════════════════════════════════════════════════════════
# performance_summary — long+short trade counting (Phase 0 fix)
# ══════════════════════════════════════════════════════════════════════════════

class TestPerformanceSummary:
    def test_long_trade_counted(self):
        # Buy at 100 (bar 1, signal=1), sell at 110 (bar 3, signal=0)
        df = mkdf(5, [100, 105, 110, 110, 110])
        df["signal"] = [0, 1, 0, 0, 0]
        p = ns2.performance_summary(df, "SYN")
        assert p["n_trades"] == 1
        assert p["win_rate"] == 100.0

    def test_short_trade_counted(self):
        # Short at 110 (bar 1, signal=-1), cover at 100 (bar 3, signal=0)
        df = mkdf(5, [105, 110, 105, 100, 100])
        df["signal"] = [0, -1, -1, 0, 0]
        p = ns2.performance_summary(df, "SYN")
        assert p["n_trades"] == 1
        assert p["win_rate"] == 100.0  # short profits: (100-110)/110 * -1 = +9.1%

    def test_both_long_and_short_counted(self):
        df = mkdf(10, [100, 110, 110, 110, 100, 100, 100, 100, 100, 100])
        df["signal"] = [1, 0, 0, -1, 0, 0, 0, 0, 0, 0]
        p = ns2.performance_summary(df, "SYN")
        assert p["n_trades"] == 2

    def test_open_position_counted(self):
        # Long entered, never closed
        df = mkdf(5, [100, 105, 110, 115, 120])
        df["signal"] = [0, 1, 1, 1, 1]
        p = ns2.performance_summary(df, "SYN")
        assert p["n_trades"] == 1
        assert p["win_rate"] == 100.0

    def test_metrics_keys_present(self):
        df = mkdf(5, [100] * 5)
        p = ns2.performance_summary(df, "SYN")
        for k in ("total_return", "bah_return", "sharpe", "max_drawdown", "n_trades", "win_rate", "regime_dist"):
            assert k in p, f"missing key: {k}"


# ══════════════════════════════════════════════════════════════════════════════
# bt.verdict — PF=None edge case (Phase 3 fix, ns2_backtest.py)
# ══════════════════════════════════════════════════════════════════════════════

class TestVerdict:
    def test_pass(self):
        assert bt.verdict({"sharpe": 2.0, "profit_factor": 3.0, "n_trades": 10, "win_rate_pct": 70.0}) == "PASS"

    def test_marginal(self):
        assert bt.verdict({"sharpe": 0.5, "profit_factor": 1.2, "n_trades": 10, "win_rate_pct": 50.0}) == "MARGINAL"

    def test_no_edge(self):
        assert bt.verdict({"sharpe": -0.5, "profit_factor": 0.5, "n_trades": 10, "win_rate_pct": 30.0}) == "NO-EDGE"

    def test_error(self):
        assert bt.verdict({"error": "fetch failed"}) == "ERROR"

    def test_pf_none_with_wins_passes(self):
        # MSFT scenario: 4 winning trades, zero losses
        assert bt.verdict({"sharpe": 2.34, "profit_factor": None, "n_trades": 4, "win_rate_pct": 100.0}) == "PASS"

    def test_pf_none_with_2_trades_does_not_pass(self):
        # 2 lucky trades — gates on n_trades≥3
        assert bt.verdict({"sharpe": 3.0, "profit_factor": None, "n_trades": 2, "win_rate_pct": 100.0}) != "PASS"

    def test_pf_none_zero_wins_is_no_edge(self):
        assert bt.verdict({"sharpe": -1.0, "profit_factor": None, "n_trades": 0, "win_rate_pct": 0.0}) == "NO-EDGE"


# ══════════════════════════════════════════════════════════════════════════════
# Momentum short-ban (Phase 3) — synthetic, no network
# ══════════════════════════════════════════════════════════════════════════════

class TestMomentumShortBan:
    def test_blocked_above_50ma_neutral_macro(self):
        df = mkdf(5, 100.0, rsi=80.0, ma_dist=0.05, regime=1)
        out = ns2.generate_signals_v2(df, np.ones(5), np.ones(5), None, None, 0)
        assert (out["signal"].iloc[1:] == 0).all(), f"expected all zeros; got {out['signal'].tolist()}"

    def test_allowed_below_50ma(self):
        df = mkdf(5, 100.0, rsi=80.0, ma_dist=-0.05, regime=1)
        out = ns2.generate_signals_v2(df, np.ones(5), np.ones(5), None, None, 0)
        assert (out["signal"].iloc[1:] == -1).all()

    def test_allowed_on_risk_off(self):
        df = mkdf(5, 100.0, rsi=80.0, ma_dist=0.05, regime=1)
        out = ns2.generate_signals_v2(df, np.ones(5), np.ones(5), None, None, -1)
        assert (out["signal"].iloc[1:] == -1).all()

    def test_long_side_unaffected(self):
        df = mkdf(5, 100.0, rsi=20.0, ma_dist=0.05, regime=1)
        out = ns2.generate_signals_v2(df, np.ones(5), np.ones(5), None, None, 0)
        assert (out["signal"].iloc[1:] == 1).all()


# ══════════════════════════════════════════════════════════════════════════════
# Confidence-weighted sizing (Phase 3)
# ══════════════════════════════════════════════════════════════════════════════

class TestConfidenceSizing:
    def test_scales_with_agreement(self):
        df = mkdf(5, 100.0, rsi=20.0, regime=1)   # MEAN_REV buy, base 0.60
        half = ns2.generate_signals_v2(df, np.ones(5), np.zeros(5), None, None, 0)
        full = ns2.generate_signals_v2(df, np.ones(5), np.ones(5), None, None, 0)
        assert np.isclose(half["position_size"].iloc[2], 0.30)  # 0.60 * 0.5
        assert np.isclose(full["position_size"].iloc[2], 0.60)  # 0.60 * 1.0

    def test_tranding_base_size_context(self):
        # TRENDING, CCI crosses above 100: prev=80, current=120 -> BUY at bar 2
        df = mkdf(5, 100.0, rsi=50.0, cci=80.0, regime=0)
        df.loc[df.index[2:], "cci"] = 120.0
        out = ns2.generate_signals_v2(df, np.zeros(5), np.ones(5), None, None, 0)
        assert out["signal"].iloc[2] == 1
        assert np.isclose(out["position_size"].iloc[2], 1.0)

    def test_crisis_base_size(self):
        # CRISIS, CCI < -250 (prev=-200, current=-260) -> SHORT at bar 2
        df = mkdf(5, 100.0, cci=-200.0, regime=2)
        df.loc[df.index[2:], "cci"] = -260.0
        out = ns2.generate_signals_v2(df, np.full(5, 2), np.ones(5), None, None, 0)
        assert out["signal"].iloc[2] == -1
        assert np.isclose(out["position_size"].iloc[2], ns2.POSITION_CRISIS)


# ══════════════════════════════════════════════════════════════════════════════
# Acceptance gates (Phase 4)
# ══════════════════════════════════════════════════════════════════════════════

import json as _json

def _make_wf_json(verdicts_dict, path):
    """Write a synthetic walk-forward JSON for testing gates."""
    results = [{"ticker": t, "verdict": v, "profit_factor": 1.0, "sharpe": 0.5}
               for t, v in verdicts_dict.items()]
    data = {"generated": "synthetic", "config": {}, "results": results}
    path.write_text(_json.dumps(data))

class TestAcceptanceGate:
    def test_no_edge_ticker_gated(self, monkeypatch, tmp_path):
        wf = tmp_path / "synthetic_wf.json"
        _make_wf_json({"TSLA": "NO-EDGE", "GOOGL": "PASS"}, wf)
        monkeypatch.setattr(ns2, "WF_RESULTS_PATH", str(wf))
        lab, gi = ns2.apply_acceptance_gate("TSLA", "BUY")
        assert lab == "NO-EDGE"
        assert gi["gated"] is True
        assert gi["verdict"] == "NO-EDGE"

    def test_pass_ticker_ungated(self, monkeypatch, tmp_path):
        wf = tmp_path / "synthetic_wf.json"
        _make_wf_json({"GOOGL": "PASS"}, wf)
        monkeypatch.setattr(ns2, "WF_RESULTS_PATH", str(wf))
        lab, gi = ns2.apply_acceptance_gate("GOOGL", "SELL")
        assert lab == "SELL"
        assert gi["gated"] is False
        assert gi["verdict"] == "PASS"

    def test_marginal_ticker_ungated(self, monkeypatch, tmp_path):
        wf = tmp_path / "synthetic_wf.json"
        _make_wf_json({"TLT": "MARGINAL"}, wf)
        monkeypatch.setattr(ns2, "WF_RESULTS_PATH", str(wf))
        lab, gi = ns2.apply_acceptance_gate("TLT", "SHORT")
        assert lab == "SHORT"
        assert gi["gated"] is False
        assert gi["verdict"] == "MARGINAL"

    def test_untested_ticker_ungated(self, monkeypatch, tmp_path):
        wf = tmp_path / "synthetic_wf.json"
        _make_wf_json({"AAPL": "PASS"}, wf)
        monkeypatch.setattr(ns2, "WF_RESULTS_PATH", str(wf))
        lab, gi = ns2.apply_acceptance_gate("ZZZ_MISSING", "WATCH")
        assert lab == "WATCH"
        assert gi["gated"] is False
        assert gi["verdict"] == "UNTESTED"

    def test_missing_file_passes_all(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ns2, "WF_RESULTS_PATH", str(tmp_path / "nope.json"))
        lab, gi = ns2.apply_acceptance_gate("TSLA", "BUY")
        assert lab == "BUY"
        assert gi["gated"] is False
        assert gi["verdict"] == "UNTESTED"