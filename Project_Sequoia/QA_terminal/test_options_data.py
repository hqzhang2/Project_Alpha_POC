"""
Hermetic tests for the option data layer (options_data.py).
No live network: options/yfinance modules are faked via sys.modules.
"""
import sys
import pandas as pd
import pytest


def _install_fake_options():
    class FakeOptions:
        def get_expirations(self, ticker):
            return ["2026-08-21", "2026-09-18"]

        def get_options_chain(self, ticker, expiry=None, use_cache=True):
            return {"ticker": ticker.upper(), "expiry": expiry,
                    "spot": 100.0, "calls": [], "puts": []}

    sys.modules["options"] = FakeOptions()
    return FakeOptions


def _install_fake_yfinance(calendar_frame=None):
    class FakeTicker:
        def __init__(self, t):
            self.calendar = calendar_frame

    class FakeYF:
        def Ticker(self, t):
            return FakeTicker(t)

    sys.modules["yfinance"] = FakeYF()
    return FakeYF


def test_yf_provider_chain_reuses_options_module(monkeypatch):
    fake = _install_fake_options()
    import options_data
    p = options_data.YFinanceProvider()
    chain = p.get_chain("spy")
    assert chain["ticker"] == "SPY" and chain["calls"] == []
    assert p.get_expirations("spy") == ["2026-08-21", "2026-09-18"]


def test_underlying_oi_history_is_stub():
    import options_data
    p = options_data.YFinanceProvider()
    assert p.get_underlying_oi_history("SPY") is None  # Step-2 gap, documented


def test_next_earnings_parses_series():
    cal = pd.DataFrame({"Earnings Date": [pd.Timestamp("2099-01-10")]},
                       index=["Earnings Date"])
    _install_fake_yfinance(cal)
    import options_data
    p = options_data.YFinanceProvider()
    assert p.get_next_earnings("SPY") == "2099-01-10"


def test_next_earnings_parses_dict_shape():
    # yfinance >= some version returns calendar as a dict with a date list
    cal = {"Earnings Date": [pd.Timestamp("2099-01-10")],
           "Earnings Average": 1.61}
    _install_fake_yfinance(cal)
    import options_data
    p = options_data.YFinanceProvider()
    assert p.get_next_earnings("SPY") == "2099-01-10"


def test_next_earnings_none_when_past():
    cal = pd.DataFrame({"Earnings Date": [pd.Timestamp("2020-01-10")]},
                       index=["Earnings Date"])
    _install_fake_yfinance(cal)
    import options_data
    p = options_data.YFinanceProvider()
    assert p.get_next_earnings("SPY") is None  # past date -> not upcoming


def test_next_earnings_none_when_missing_or_broken():
    import options_data
    _install_fake_yfinance(None)                      # no calendar at all
    assert options_data.YFinanceProvider().get_next_earnings("SPY") is None
    bad = pd.DataFrame(index=["Dividend Date"], data={"x": [1]})
    _install_fake_yfinance(bad)                       # no Earnings Date row
    assert options_data.YFinanceProvider().get_next_earnings("SPY") is None


def test_get_provider_defaults_to_yfinance(monkeypatch):
    import sys as _sys
    monkeypatch.setattr(_sys, "modules", {k: v for k, v in _sys.modules.items()
                                          if not k.startswith("options_data")})
    import config
    monkeypatch.setattr(config, "OPTION_DATA_PROVIDER", "yfinance", raising=False)
    import options_data
    options_data._PROVIDER = None  # reset singleton
    assert isinstance(options_data.get_provider(), options_data.YFinanceProvider)
