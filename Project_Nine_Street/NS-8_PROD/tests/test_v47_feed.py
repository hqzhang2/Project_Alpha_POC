#!/usr/bin/env python3
"""Test v4.7 — NS-8 feed contract + live==validated sizing (DPF-owned tests).

Covers research_ns8_feed_v47.md:
  - P0: inverse-vol sizing in the live pipeline path (incl. closes→returns)
  - feed-contract metadata threading through build_signal_document
  - gross_risk_exposure / eff_n (risky-only Herfindahl) definitions
  - fixed-method legacy fallback
"""
import pytest

import config
import signals
import pipeline


# ── closes→returns derivation ────────────────────────────────────────────
def test_daily_returns_from_closes_basic():
    closes = {"SPY": [100.0, 110.0, 99.0, 105.0]}
    out = pipeline._daily_returns_from_closes(closes, window=5)
    assert len(out["SPY"]) == 3
    assert out["SPY"][0] == pytest.approx(0.10)
    assert out["SPY"][1] == pytest.approx(99.0 / 110.0 - 1.0)
    assert out["SPY"][2] == pytest.approx(105.0 / 99.0 - 1.0)


def test_daily_returns_skips_zero_prev():
    # prev=0 is falsy → that ratio is skipped; remaining rets still valid
    closes = {"X": [0.0, 100.0, 110.0, 121.0, 133.1]}
    out = pipeline._daily_returns_from_closes(closes, window=5)
    assert out["X"] == [pytest.approx(r) for r in (0.10, 0.10, 0.10)]


def test_daily_returns_insufficient_history_dropped():
    closes = {"A": [1.0, 2.0], "B": [1.0] * 20}
    out = pipeline._daily_returns_from_closes(closes, window=5)
    assert "A" not in out          # only 1 ret < 3 obs minimum
    assert "B" in out


# ── sizing: inverse_vol is the live default; fixed is legacy fallback ────
def _fake_prices():
    """Two risky assets with clearly different vols + enough SMA history."""
    steady = [100.0 * (1 + 0.001 * i) for i in range(300)]      # low vol, uptrend
    wild = [100.0 * (1 + (0.02 if i % 2 else -0.015)) for i in range(300)]
    return {"AAA": steady, "BBB": wild}


def test_inverse_vol_sizing_scales_by_inv_vol(monkeypatch):
    monkeypatch.setattr(config, "SIZING_METHOD", "inverse_vol")
    prices = _fake_prices()
    sigs = {t: 1 for t in prices}
    rets = pipeline._daily_returns_from_closes(prices, window=60)
    vols = {t: __import__("vol").exante_vol(rets.get(t, [])) for t in prices}
    w = signals.compute_weights_inverse_vol(sigs, vols)
    # both in-trend → each gets ASSET_WEIGHT total; lower-vol name weighted more
    assert w["AAA"] > w["BBB"] > 0
    assert w[config.CASH_PROXY] == pytest.approx(1 - w["AAA"] - w["BBB"], abs=1e-9)


def test_fixed_method_falls_back_to_compute_weights(monkeypatch):
    monkeypatch.setattr(config, "SIZING_METHOD", "fixed")
    sigs = {"AAA": 1, "BBB": 0}
    w = signals.compute_weights(sigs)
    assert w["AAA"] == config.ASSET_WEIGHT
    assert w["BBB"] == 0.0


# ── feed contract metadata ───────────────────────────────────────────────
def test_signal_document_carries_v47_metadata():
    weights = {"SPY": 0.15, "DBC": 0.05, "SHV": 0.80}
    doc = signals.build_signal_document("2026-08-25", {}, weights, version=1,
                                        vols={"SPY": 0.12, "SHV": None})
    assert doc["service"] == "NS-8"
    assert doc["strategy"] == "tactical_aa"
    assert doc["method"] == config.SIZING_METHOD
    assert doc["signal_method"] == config.SIGNAL_METHOD
    assert doc["guardrails"] == {"max_weight": 0.35, "min_eff_n": 2,
                                 "cash_floor_pct": 0.0}
    assert doc["gross_risk_exposure"] == pytest.approx(0.20)
    assert doc["max_weight"] == pytest.approx(0.80)
    # risky-only Herfindahl: 1/(0.15²+0.05²) = 1/0.025 = 40.0
    assert doc["eff_n"] == pytest.approx(40.0)
    assert doc["exante_vol"]["SPY"] == pytest.approx(0.12)
    assert doc["exante_vol"]["SHV"] is None


def test_eff_n_excludes_cash_proxy():
    # all-cash book: no risky holdings → eff_n 0, exposure 0
    doc = signals.build_signal_document("2026-08-25", {},
                                        {config.CASH_PROXY: 1.0})
    assert doc["eff_n"] == 0.0
    assert doc["gross_risk_exposure"] == pytest.approx(0.0)


def test_gross_risk_uses_config_cash_proxy_not_literal():
    # If the proxy changed, the KPI must follow config, not a hardcoded SHV.
    monkey = pytest.MonkeyPatch()
    monkey.setattr(config, "CASH_PROXY", "CASHX")
    try:
        doc = signals.build_signal_document("2026-08-25", {},
                                            {"SPY": 0.3, "CASHX": 0.7})
        assert doc["gross_risk_exposure"] == pytest.approx(0.30)
        assert doc["eff_n"] == pytest.approx(round(1 / 0.09, 2))
    finally:
        monkey.undo()
