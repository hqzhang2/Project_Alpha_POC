#!/usr/bin/env python3
"""
Tests for the Ratio page backend cache (server._ratio_close, 2026-08-10).

Network-free: yf.Ticker is monkeypatched to a fake. Exercises the per-ticker
close cache: dedupe within TTL, refetch after expiry, per-period keys,
fail-open on network error, and the get_ratio_data payload shape.
"""
import os
import sys
import time

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import server


class _FakeTicker:
    """Stand-in for yf.Ticker: history() returns {'Close': <Series>}."""

    def __init__(self, close):
        self._close = close

    def history(self, period=None):
        return {"Close": self._close}


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    """Module-level caches leak across tests — give each test a clean one."""
    monkeypatch.setattr(server, "_ratio_close_cache", {})
    monkeypatch.setattr(server, "_ratio_fetch_locks", {})


def _install_fake(monkeypatch, closes):
    """closes: {ticker: Series}. Records downloads in .calls."""
    calls = []
    real = server.yf

    class FakeYf:
        @staticmethod
        def Ticker(sym):
            calls.append(sym)
            return _FakeTicker(closes[sym])

    monkeypatch.setattr(server, "yf", FakeYf())
    return calls


def test_ratio_close_dedupes_within_ttl(monkeypatch):
    s = pd.Series([1.0, 2.0, 3.0], dtype=float)
    calls = _install_fake(monkeypatch, {"SPY": s})
    out1 = server._ratio_close("SPY", "1y")
    out2 = server._ratio_close("SPY", "1y")
    assert calls == ["SPY"]          # downloaded once
    assert out1 is out2              # same cached object


def test_ratio_close_distinct_per_period(monkeypatch):
    s = pd.Series([1.0], dtype=float)
    calls = _install_fake(monkeypatch, {"SPY": s})
    server._ratio_close("SPY", "1y")
    server._ratio_close("SPY", "max")
    assert calls == ["SPY", "SPY"]   # different period = different key


def test_ratio_close_refetches_after_ttl(monkeypatch):
    s = pd.Series([1.0], dtype=float)
    calls = _install_fake(monkeypatch, {"SPY": s})
    server._ratio_close("SPY", "1y")
    # age the entry past TTL, then force a refetch
    server._ratio_close_cache["SPY:1y"] = (time.time() - server.RATIO_CLOSE_TTL - 1, s)
    server._ratio_close("SPY", "1y")
    assert calls == ["SPY", "SPY"]


def test_ratio_close_fail_open(monkeypatch):
    class BoomTicker:
        def history(self, period=None):
            raise OSError("network down")
    calls = []

    class FakeYf:
        @staticmethod
        def Ticker(sym):
            calls.append(sym)
            return BoomTicker()

    monkeypatch.setattr(server, "yf", FakeYf())
    out = server._ratio_close("SPY", "1y")
    assert out.empty                      # fail-open, not a crash
    assert server._ratio_close("SPY", "1y").empty  # cached, still no crash


def test_get_ratio_data_payload_shape(monkeypatch):
    idx = pd.date_range("2025-01-01", periods=300, freq="B")
    closes = {t: pd.Series(100.0 + i * 0.1, index=idx, dtype=float)
              for i, t in enumerate(["IBIT", "SPY"])}
    _install_fake(monkeypatch, closes)
    h = object.__new__(server.Handler)  # no __init__ needed — method uses no state
    payload = h.get_ratio_data("IBIT", "SPY", "1Y", 200)
    assert payload["t1_name"] == "IBIT" and payload["t2_name"] == "SPY"
    assert len(payload["ratio"]) == 300
    for k in ("sma", "rsi", "macd", "macd_signal", "macd_hist", "upper", "lower"):
        assert len(payload[k]) == 300
