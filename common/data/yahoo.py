#!/usr/bin/env python3
"""
Common Data Library
Unified Yahoo Finance client with caching and error handling.
"""
import math
import time
import warnings
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore", category=RuntimeWarning)


class YahooClient:
    """
    Unified Yahoo Finance client with:
    - TTL caching
    - Rate limiting
    - Batch fetching
    - Safe value extraction
    """

    def __init__(self, cache_ttl: int = 300, rate_limit: float = 0.05, timeout: int = 30, retries: int = 3):
        self.cache_ttl = cache_ttl
        self.rate_limit = rate_limit
        self.timeout = timeout
        self.retries = retries

        self._cache = {}
        self._last_request = 0

    def _rate_limit(self):
        """Enforce minimum time between requests."""
        elapsed = time.time() - self._last_request
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_request = time.time()

    def _cache_key(self, method: str, *args, **kwargs) -> str:
        return f"{method}:{args}:{sorted(kwargs.items())}"

    def _get_cached(self, key: str) -> Optional[any]:
        if key in self._cache:
            value, expiry = self._cache[key]
            if time.time() < expiry:
                return value
            del self._cache[key]
        return None

    def _set_cached(self, key: str, value: any):
        self._cache[key] = (value, time.time() + self.cache_ttl)

    def _fetch_with_retry(self, func, *args, **kwargs):
        """Execute with retries."""
        last_error = None
        for attempt in range(self.retries):
            try:
                self._rate_limit()
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e
                if attempt < self.retries - 1:
                    time.sleep(0.5 * (attempt + 1))
        raise last_error

    # --- Safe value extraction ---

    @staticmethod
    def safe_float(val) -> Optional[float]:
        """Safely convert to float, return None for NaN/inf/invalid."""
        if val is None:
            return None
        try:
            f = float(val)
            return None if math.isnan(f) or math.isinf(f) else f
        except (TypeError, ValueError):
            return None

    @staticmethod
    def safe_int(val) -> Optional[int]:
        """Safely convert to int."""
        f = YahooClient.safe_float(val)
        return int(f) if f is not None else None

    @staticmethod
    def safe_pct(current: float, past: float) -> Optional[float]:
        """Safe percentage change."""
        if current is None or past is None or past == 0:
            return None
        try:
            ret = ((current / past) - 1) * 100
            return None if math.isnan(ret) or math.isinf(ret) else ret
        except (TypeError, ValueError, ZeroDivisionError):
            return None

    # --- Data fetching ---

    def get_ticker(self, symbol: str) -> yf.Ticker:
        """Get yfinance Ticker object (cached)."""
        key = self._cache_key("ticker", symbol)
        cached = self._get_cached(key)
        if cached:
            return cached

        ticker = yf.Ticker(symbol)
        self._set_cached(key, ticker)
        return ticker

    def get_info(self, symbol: str) -> dict:
        """Get ticker info dict."""
        key = self._cache_key("info", symbol)
        cached = self._get_cached(key)
        if cached:
            return cached

        ticker = self.get_ticker(symbol)
        info = self._fetch_with_retry(ticker.info)
        self._set_cached(key, info)
        return info

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d",
                    start: str = None, end: str = None) -> pd.DataFrame:
        """Get historical OHLCV data."""
        key = self._cache_key("history", symbol, period, interval, start, end)
        cached = self._get_cached(key)
        if cached is not None:
            return cached

        ticker = self.get_ticker(symbol)
        if start and end:
            df = self._fetch_with_retry(ticker.history, start=start, end=end, interval=interval)
        else:
            df = self._fetch_with_retry(ticker.history, period=period, interval=interval)

        self._set_cached(key, df)
        return df

    def get_weekly(self, symbol: str, weeks: int = 52) -> pd.DataFrame:
        """Get weekly data for lookback period."""
        end = datetime.now()
        start = end - timedelta(weeks=weeks + 4)
        return self.get_history(symbol, start=start.strftime("%Y-%m-%d"),
                                end=end.strftime("%Y-%m-%d"), interval="1wk")

    def get_multiple_history(self, symbols: list[str], period: str = "1y",
                              interval: str = "1d") -> dict[str, pd.DataFrame]:
        """Batch fetch for multiple symbols (single yfinance call)."""
        if not symbols:
            return {}

        key = self._cache_key("multi_history", tuple(sorted(symbols)), period, interval)
        cached = self._get_cached(key)
        if cached is not None:
            return cached

        self._rate_limit()
        try:
            raw = yf.download(
                tickers=symbols,
                period=period,
                interval=interval,
                group_by="ticker",
                auto_adjust=True,
                progress=False,
                threads=True,
                timeout=self.timeout
            )
        except Exception:
            # Fallback to individual fetches
            result = {}
            for sym in symbols:
                try:
                    result[sym] = self.get_history(sym, period, interval)
                except Exception:
                    result[sym] = pd.DataFrame()
            self._set_cached(key, result)
            return result

        result = {}
        if isinstance(raw.columns, pd.MultiIndex):
            for sym in symbols:
                if sym in raw.columns.get_level_values(0):
                    df = raw[sym].dropna()
                    result[sym] = df
                else:
                    result[sym] = pd.DataFrame()
        else:
            # Single symbol
            result[symbols[0]] = raw.dropna()

        self._set_cached(key, result)
        return result

    def get_quotes(self, symbols: list[str]) -> dict[str, dict]:
        """Get quotes for multiple symbols."""
        results = {}
        for sym in symbols:
            results[sym.upper()] = self.get_quote(sym)
            time.sleep(0.05)  # Small delay between quote requests
        return results

    def get_quote(self, symbol: str) -> dict:
        """Get comprehensive quote data for a symbol."""
        key = self._cache_key("quote", symbol)
        cached = self._get_cached(key)
        if cached:
            return cached

        info = self.get_info(symbol)

        # Calculate returns from history
        hist = self.get_history(symbol, period="5y")
        returns = self._compute_returns(hist)

        quote = {
            "ticker": symbol.upper(),
            "name": info.get("shortName", info.get("longName", symbol)),
            "price": self.safe_float(info.get("currentPrice", info.get("regularMarketPrice"))),
            "change": self.safe_float(info.get("regularMarketChange")),
            "change_pct": self.safe_float(info.get("regularMarketChangePercent")),
            "open": self.safe_float(info.get("regularMarketOpen")),
            "high": self.safe_float(info.get("regularMarketDayHigh")),
            "low": self.safe_float(info.get("regularMarketDayLow")),
            "volume": self.safe_float(info.get("regularMarketVolume")),
            "avg_volume": self.safe_float(info.get("averageVolume")),
            "market_cap": self.safe_float(info.get("marketCap")),
            "pe_ratio": self.safe_float(info.get("trailingPE")),
            "dividend_yield": self.safe_float(info.get("dividendYield")),
            "52w_high": self.safe_float(info.get("fiftyTwoWeekHigh")),
            "52w_low": self.safe_float(info.get("fiftyTwoWeekLow")),
            **returns,
            "timestamp": time.time()
        }

        self._set_cached(key, quote)
        return quote

    def _compute_returns(self, hist: pd.DataFrame) -> dict:
        """Compute various period returns from history."""
        returns = {
            "ret_1d": None, "ret_1w": None, "ret_1m": None,
            "ret_3m": None, "ret_ytd": None, "ret_1y": None, "ret_5y": None
        }

        if hist.empty or "Close" not in hist.columns:
            return returns

        closes = hist["Close"].dropna()
        if len(closes) < 2:
            return returns

        latest = self.safe_float(closes.iloc[-1])
        if latest is None:
            return returns

        tz = closes.index.tz
        now = pd.Timestamp.now(tz=tz) if tz else pd.Timestamp.now()

        def get_price_at(days_ago: int) -> Optional[float]:
            target = now - timedelta(days=days_ago)
            past_data = closes[closes.index <= target]
            return self.safe_float(past_data.iloc[-1]) if len(past_data) > 0 else None

        def get_ytd() -> Optional[float]:
            ytd_start = pd.Timestamp(year=now.year, month=1, day=1, tz=tz)
            ytd_data = closes[closes.index >= ytd_start]
            if len(ytd_data) > 1:
                return self.safe_float(ytd_data.iloc[0])
            return None

        ret_1d = get_price_at(1) if len(closes) > 1 else None
        returns["ret_1d"] = self.safe_pct(latest, ret_1d)
        returns["ret_1w"] = self.safe_pct(latest, get_price_at(7))
        returns["ret_1m"] = self.safe_pct(latest, get_price_at(30))
        returns["ret_3m"] = self.safe_pct(latest, get_price_at(90))
        returns["ret_ytd"] = self.safe_pct(latest, get_ytd())
        returns["ret_1y"] = self.safe_pct(latest, get_price_at(365))
        returns["ret_5y"] = self.safe_pct(latest, get_price_at(1825))

        return returns

    def get_etf_holdings(self, symbol: str, limit: int = 50) -> dict[str, float]:
        """Get ETF top holdings with weights."""
        key = self._cache_key("etf_holdings", symbol, limit)
        cached = self._get_cached(key)
        if cached:
            return cached

        ticker = self.get_ticker(symbol)
        holdings = {}

        try:
            if hasattr(ticker, 'funds_data') and ticker.funds_data is not None:
                top_holdings = ticker.funds_data.top_holdings
                if top_holdings is not None and not top_holdings.empty:
                    for _, row in top_holdings.head(limit).iterrows():
                        sym = str(row.get("symbol", row.get("Symbol", ""))).strip()
                        weight = self.safe_float(row.get("holdingPercent", row.get("Holding Percent", row.get("weight", 0))))
                        if sym and weight:
                            holdings[sym] = weight
        except Exception:
            pass

        self._set_cached(key, holdings)
        return holdings

    def get_fundamentals(self, symbol: str) -> dict:
        """Get key fundamental data."""
        ticker = self.get_ticker(symbol)
        info = self.get_info(symbol)

        # Try to get financial statements
        bs = is_ = cf = None
        try:
            bs = ticker.balance_sheet
            is_ = ticker.income_stmt
            cf = ticker.cashflow
        except Exception:
            pass

        return {
            "info": info,
            "balance_sheet": bs,
            "income_stmt": is_,
            "cashflow": cf
        }

    def clear_cache(self):
        """Clear all caches."""
        self._cache.clear()


# Singleton instance
_yahoo_client: Optional[YahooClient] = None


def get_yahoo_client() -> YahooClient:
    """Get or create the singleton YahooClient."""
    global _yahoo_client
    if _yahoo_client is None:
        from common.config import get_common_config
        cfg = get_common_config()
        _yahoo_client = YahooClient(
            cache_ttl=cfg.yfinance_cache_ttl,
            rate_limit=cfg.yfinance_rate_limit,
            timeout=cfg.yfinance_timeout,
            retries=cfg.yfinance_retries
        )
    return _yahoo_client


# Convenience functions
def get_quote(symbol: str) -> dict:
    return get_yahoo_client().get_quote(symbol)


def get_quotes(symbols: list[str]) -> dict[str, dict]:
    return get_yahoo_client().get_quotes(symbols)


def get_history(symbol: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    return get_yahoo_client().get_history(symbol, period, interval)


def get_weekly(symbol: str, weeks: int = 52) -> pd.DataFrame:
    return get_yahoo_client().get_weekly(symbol, weeks)


def get_etf_holdings(symbol: str, limit: int = 50) -> dict[str, float]:
    return get_yahoo_client().get_etf_holdings(symbol, limit)


def get_fundamentals(symbol: str) -> dict:
    return get_yahoo_client().get_fundamentals(symbol)


if __name__ == "__main__":
    # Quick test
    client = get_yahoo_client()
    print("Testing YahooClient...")
    print("SPY quote:", client.get_quote("SPY"))
    print("Multi:", client.get_quotes(["SPY", "QQQ", "TLT"]))