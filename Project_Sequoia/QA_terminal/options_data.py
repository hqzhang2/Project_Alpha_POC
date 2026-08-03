"""
Option data layer (v2.4). Vendor-agnostic: screener code never imports
yfinance/moomoo directly. YFinanceProvider now; MoomooProvider lands in
Step 2 behind the same interface (config.OPTION_DATA_PROVIDER == 'moomoo').

The chain shape returned by get_chain() is EXACTLY options.get_options_chain's
shape ({ticker, expiry, spot, calls: [records], puts: [records], ...}) so all
Greek/parity/IV math in options.py is reused untouched.
"""
import datetime as _dt
from typing import Optional

import config


class ProviderUnavailableError(Exception):
    """Raised when a requested data provider cannot serve (not built / not set up)."""


class OptionDataProvider:
    """Interface every feed implements."""

    name = "base"

    def get_expirations(self, ticker: str) -> list:
        raise NotImplementedError

    def get_chain(self, ticker: str, expiry: Optional[str] = None) -> dict:
        raise NotImplementedError

    def get_next_earnings(self, ticker: str) -> Optional[str]:
        """ISO date (YYYY-MM-DD) of next earnings, or None if unknown."""
        raise NotImplementedError

    def get_underlying_oi_history(self, ticker: str):
        """Daily total-OI/PCR time series for the ticker. STUB on yfinance (None);
        real on Moomoo (Step 2)."""
        return None


class YFinanceProvider(OptionDataProvider):
    """Delegates chains to options.py (Greeks/parity/IV reuse) — never reimplement."""

    name = "yfinance"

    def get_expirations(self, ticker):
        import options
        return options.get_expirations(ticker)

    def get_chain(self, ticker, expiry=None):
        import options
        return options.get_options_chain(ticker, expiry, use_cache=True)

    def get_next_earnings(self, ticker):
        """yfinance `calendar` is a dict on some versions ({'Earnings Date': [date,...]})
        and a DataFrame on others (index contains 'Earnings Date'). Handle both."""
        try:
            import yfinance
            import pandas as pd
            cal = yfinance.Ticker(ticker).calendar
            if cal is None:
                return None
            v = None
            if isinstance(cal, dict):
                v = cal.get("Earnings Date")
            elif hasattr(cal, "index") and "Earnings Date" in cal.index:
                v = cal.loc["Earnings Date"]
                if isinstance(v, pd.Series):
                    v = v.iloc[0] if len(v) else None
            if v is None or (isinstance(v, float) and v != v):  # NaN
                return None
            if isinstance(v, (list, tuple)) or (hasattr(v, "__len__") and not isinstance(v, str)):
                v = v[0] if len(v) else None
            if v is None:
                return None
            d = pd.Timestamp(v).date()
            return d.isoformat() if d >= _dt.date.today() else None
        except Exception:
            return None


_PROVIDER = None


def _provider_class(name):
    name = name or getattr(config, "OPTION_DATA_PROVIDER", "yfinance")
    if name == "yfinance":
        return YFinanceProvider
    if name == "moomoo":
        from options_data_moomoo import MoomooProvider
        return MoomooProvider
    raise ProviderUnavailableError(f"unknown data provider '{name}'")


def get_provider(name=None):
    """Return the singleton provider for `name` (default: config.OPTION_DATA_PROVIDER).
    Swapping name swaps the instance (Moomoo holds one OpenD connection)."""
    global _PROVIDER
    name = name or getattr(config, "OPTION_DATA_PROVIDER", "yfinance")
    if _PROVIDER is None or _PROVIDER.name != name:
        cls = _provider_class(name)
        if getattr(cls, "IMPLEMENTED", True) is not True:
            raise ProviderUnavailableError(
                f"provider '{name}' unavailable: {getattr(cls, 'UNAVAILABLE_REASON', 'not implemented')}")
        _PROVIDER = cls()
    return _PROVIDER


def set_provider(name):
    """Force a provider swap (UI toggle). Keeps the existing instance when the
    name is unchanged (avoids needless reconnects). Returns the provider."""
    global _PROVIDER
    if _PROVIDER is not None and _PROVIDER.name == name:
        return _PROVIDER
    _PROVIDER = None
    return get_provider(name)


def provider_status():
    """{name: usable} for the UI toggle. 'moomoo' reflects the mapping stub state,
    not the OpenD gateway (that check happens when the real provider lands)."""
    status = {"yfinance": True}
    try:
        cls = _provider_class("moomoo")
        status["moomoo"] = getattr(cls, "IMPLEMENTED", True) is True
    except Exception:
        status["moomoo"] = False
    return status
