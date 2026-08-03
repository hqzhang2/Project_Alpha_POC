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


class OptionDataProvider:
    """Interface every feed implements."""

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


def get_provider() -> OptionDataProvider:
    global _PROVIDER
    if _PROVIDER is None:
        import config
        if getattr(config, "OPTION_DATA_PROVIDER", "yfinance") == "moomoo":
            from options_data_moomoo import MoomooProvider  # Step 2 artifact
            _PROVIDER = MoomooProvider()
        else:
            _PROVIDER = YFinanceProvider()
    return _PROVIDER
