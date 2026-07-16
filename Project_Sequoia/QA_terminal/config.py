"""
Alpha Terminal Configuration
"""
import os

# Server config
DEFAULT_PORT = 9098
HOST = '0.0.0.0'

# Yahoo Finance config
YF_CACHE_MINUTES = 5

# Chart timeframes
TIMEFRAME_MAP = {
    '1D': '1d',
    '1W': '1wk', 
    '1M': '1mo',
    '3M': '3mo',
    'YTD': 'ytd',
    '1Y': '1y',
    '5Y': '5y'
}

# Default watchlists
DEFAULT_WATCHLIST = ['SPY', 'QQQ', 'IWM', 'TLT']
RATIO_WATCHLIST = ['XLE/SPY', 'TLT/SPY', 'GLD/SPY']

# Options config
OTM_STRIKES = 10  # Number of OTM strikes to show
STANDARD_EXPIRY_DAY_RANGE = (15, 21)  # Days for standard 3rd Friday

# Indicator defaults
DEFAULT_SMA_PERIOD = 200
RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# UI config
THEME = 'dark'
CHART_COLORS = {
    'primary': '#3fb950',
    'secondary': '#f0883e', 
    'calls': '#3fb950',
    'puts': '#f85149',
    'volume': 'rgba(88, 166, 255, 0.3)'
}
