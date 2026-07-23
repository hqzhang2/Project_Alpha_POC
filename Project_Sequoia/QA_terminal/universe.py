"""
Reference universe of NYSE/NASDAQ-listed large-cap stocks.

yfinance does not expose index constituents programmatically, so we use the
stable, public S&P 500 + NASDAQ-100 membership lists (the bulk of liquid
NYSE/NASDAQ volume). This is the scan universe for the 52-week-high feature.

Tickers are the common symbols; exchange is resolved at scan time from each
security's `info` (NMS/NGS -> NASDAQ, NYQ -> NYSE).
"""

# S&P 500 (as of the standard 2024 membership; stable for our purposes)
SP500 = [
    "A", "AAL", "AAP", "AAPL", "ABBV", "ABC", "ABMD", "ABT", "ACN", "ADBE",
    "ADI", "ADM", "ADP", "ADSK", "AEE", "AEP", "AES", "AFL", "AIG", "AIZ",
    "AJG", "AKAM", "ALB", "ALGN", "ALK", "ALL", "ALLE", "AMAT", "AMCR", "AMD",
    "AME", "AMGN", "AMP", "AMT", "ANET", "ANSS", "AON", "AOS", "APA", "APD",
    "APH", "APTV", "ARE", "ATO", "AVB", "AVGO", "AVY", "AWK", "AXON", "AXP",
    "AZO", "BA", "BAC", "BAX", "BBWI", "BBY", "BDX", "BEPC", "BF-B", "BG",
    "BIIB", "BK", "BKNG", "BKR", "BLK", "BMY", "BR", "BSX", "BWA", "BXP",
    "C", "CAG", "CAH", "CARR", "CAT", "CB", "CBI", "CBRE", "CCI", "CCL",
    "CDNS", "CDW", "CE", "CF", "CFG", "CHD", "CHRW", "CHTR", "CI", "CINF",
    "CL", "CLX", "CMA", "CMCSA", "CME", "CMI", "CMS", "CNC", "CNP", "COF",
    "COO", "COP", "COST", "CPB", "CPRT", "CRWD", "CSCO", "CSX", "CTAS", "CTLT",
    "CTSH", "CTVA", "CVS", "CVX", "CZR", "D", "DAL", "DAR", "DAY", "DBX",
    "DD", "DE", "DECK", "DFS", "DG", "DGX", "DHI", "DHR", "DIS", "DLR",
    "DLTR", "DOW", "DPZ", "DRE", "DRI", "DTE", "DUK", "DVA", "DVN", "DXC",
    "DXCM", "EA", "EBAY", "ECL", "ED", "EFX", "EG", "EIX", "EL", "EMN",
    "EMR", "ENPH", "EOG", "EPAM", "EQIX", "EQR", "ES", "ESS", "ETN", "ETR",
    "EVRG", "EW", "EXC", "EXP", "EXPD", "EXR", "F", "FANG", "FAST", "FDS",
    "FDX", "FE", "FFIV", "FITB", "FLEX", "FLT", "FMC", "FOXA", "FOX", "FRC",
    "FTNT", "FTV", "GD", "GE", "GEHC", "GEN", "GILD", "GIS", "GL", "GLOB",
    "GLW", "GM", "GNRC", "GOOG", "GOOGL", "GPC", "GPN", "GRMN", "GS", "GWW",
    "HAL", "HAS", "HBAN", "HBI", "HCA", "HD", "HES", "HIG", "HII", "HLT",
    "HOLX", "HON", "HPE", "HPQ", "HRL", "HSIC", "HST", "HSY", "HUM", "HWM",
    "IBM", "IBKR", "ICE", "IDXX", "IFF", "ILMN", "INCY", "INTC", "INTU", "IP",
    "IPG", "IQV", "IR", "ISRG", "IT", "ITW", "IVZ", "J", "JBHT", "JBL",
    "JCI", "JKHY", "JNJ", "JNPR", "JPM", "K", "KEY", "KHC", "KIM", "KLAC",
    "KMB", "KMI", "KMX", "KO", "KR", "L", "LDOS", "LEN", "LII", "LIN",
    "LKQ", "LLY", "LMT", "LNC", "LOW", "LULU", "LVS", "LW", "LYB", "LYV",
    "MA", "MAA", "MAR", "MAS", "MCD", "MCHP", "MCK", "MCO", "MDLZ", "MDT",
    "MET", "MGM", "MHK", "MKC", "MKTX", "MLM", "MMC", "MMM", "MNST", "MO",
    "MOS", "MPC", "MRK", "MRO", "MS", "MSCI", "MSFT", "MSI", "MTB", "MTD",
 "MU", "MXIM", "MSCI", "NDAQ", "NEE", "NEM", "NFLX", "NI", "NKE", "NOC",
    "NOV", "NRG", "NSC", "NTAP", "NTRS", "NUE", "NVDA", "NVR", "NWS", "NWSA",
    "O", "ODFL", "OKE", "OMC", "ON", "ORLY", "OTIS", "OXY", "PACCAR", "PANW",
    "PANW", "PARA", "PAYC", "PAYX", "PCAR", "PEAK", "PEG", "PENN", "PEP",
    "PFE", "PFG", "PG", "PGR", "PH", "PHM", "PKG", "PLD", "PLTR", "PM",
    "PNC", "PNR", "PNW", "POOL", "PPG", "PPL", "PRU", "PSA", "PSX", "PVH",
    "PWR", "PX", "PYPL", "QCOM", "QRVO", "RJF", "RL", "RMD", "ROK", "ROP",
    "ROST", "RSG", "RTX", "RVTY", "SBAC", "SBUX", "SCHW", "SHW",
    "SJM", "SLB", "SNA", "SNPS", "SO", "SPG", "SPGI", "SRE", "STE", "STLD",
    "STT", "STZ", "SWK", "SWKS", "SYF", "SYK", "SYY", "T", "TAP", "TDG",
    "TDY", "TFX", "TGT", "TJX", "TKO", "TMO", "TMUS", "TPR", "TRMB", "TROW",
    "TRV", "TSCO", "TSLA", "TT", "TTWO", "TXN", "TXT", "TYL", "UA", "UAA",
    "UAL", "UDR", "UHS", "ULTA", "UNH", "UNP", "UPS", "URI", "USB", "V",
    "VFC", "VICI", "VLO", "VLY", "VMC", "VNO", "VRSK", "VRSN", "VRTX", "VTR",
    "VZ", "WAB", "WAT", "WBA", "WDC", "WEC", "WELL", "WFC", "WHR", "WMB",
    "WM", "WMT", "WRB", "WST", "WTW", "WY", "WYNN", "XEL", "XOM", "XRAY",
    "XYL", "YUM", "ZBH", "ZBRA", "ZION", "ZTS",
]

# NASDAQ-100 (overlaps with S&P 500; deduped at runtime)
NASDAQ100 = [
    "AAPL", "ADBE", "ADI", "ADP", "ADSK", "AEP", "AMAT", "AMD", "AMGN", "AMZN",
    "ANSS", "ASML", "ATVI", "AVGO", "AXON", "BIDU", "BIIB", "BKNG", "CDNS",
    "CDW", "CHKP", "CMCSA", "COST", "CPRT", "CRWD", "CSCO", "CSX", "CTAS",
    "CTSH", "DLTR", "DXCM", "EA", "EBAY", "EXC", "FISV", "FOX", "FOXA", "GILD",
    "GOOG", "GOOGL", "HON", "IDXX", "ILMN", "INCY", "INTC", "INTU", "ISRG",
    "JD", "KDP", "KHC", "KLAC", "LRCX", "LULU", "MAR", "MCHP", "MDLZ", "MELI",
    "MNST", "MRNA", "MRVL", "MSFT", "MDLZ", "MU", "NFLX", "NTES", "NVDA",
    "NXPI", "ORLY", "PAYX", "PCAR", "PEP", "PYPL", "QCOM", "REGN", "ROST",
    "SBUX", "SIRI", "SNPS", "SWKS", "TCOM", "TEAM", "TMUS", "TSLA", "TXN",
    "VRSK", "VRTX", "WDAY", "XEL", "ZM", "ZS",
]


def get_universe():
    """Return the de-duplicated S&P 500 + NASDAQ-100 universe."""
    return sorted(set(SP500) | set(NASDAQ100))


# Exchange code -> display mapping (from yfinance `info['exchange']`)
EXCHANGE_MAP = {
    "NMS": "NASDAQ",
    "NGS": "NASDAQ",
    "NGM": "NASDAQ",
    "NYQ": "NYSE",
    "NYS": "NYSE",
    "NYE": "NYSE",
    "PCX": "NYSE",  # NYSE Arca (ETF) — treat as NYSE family
    "BTS": "NYSE",  # NYSE Arca / Cboe
}
