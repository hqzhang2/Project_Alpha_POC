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


def test_get_provider_named_yfinance(monkeypatch):
    import options_data
    options_data._PROVIDER = None
    p = options_data.get_provider("yfinance")
    assert isinstance(p, options_data.YFinanceProvider) and p.name == "yfinance"
    # singleton: same instance on repeat call
    assert options_data.get_provider("yfinance") is p


def test_get_provider_moomoo_raises_gracefully():
    import options_data
    options_data._PROVIDER = None
    try:
        options_data.get_provider("moomoo")
        assert False, "expected ProviderUnavailableError"
    except options_data.ProviderUnavailableError as e:
        assert "moomoo" in str(e) and "unavailable" in str(e)


def test_set_provider_swaps(monkeypatch):
    import options_data
    options_data._PROVIDER = None
    a = options_data.get_provider("yfinance")
    b = options_data.set_provider("yfinance")
    assert b is a  # same singleton
    c = options_data.get_provider()
    assert c is a   # default resolves to same singleton


def test_provider_status_shape():
    import options_data
    st = options_data.provider_status()
    assert st["yfinance"] is True
    assert st["moomoo"] is False  # stub not implemented yet
    assert set(st) == {"yfinance", "moomoo", "polygon"}


def test_get_provider_polygon_requires_key(monkeypatch):
    import options_data
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    options_data._PROVIDER = None
    try:
        options_data.get_provider("polygon")
        assert False, "expected ProviderUnavailableError"
    except options_data.ProviderUnavailableError as e:
        assert "polygon" in str(e) and "POLYGON_API_KEY" in str(e)


def test_provider_status_polygon_key_gated(monkeypatch):
    import options_data
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    assert options_data.provider_status()["polygon"] is False
    monkeypatch.setenv("POLYGON_API_KEY", "test-key")
    assert options_data.provider_status()["polygon"] is True
