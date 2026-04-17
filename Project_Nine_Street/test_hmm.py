import pandas as pd
from ns_backtester import NSBacktester

bt = NSBacktester(tickers=["SPY"], start_date="2022-01-01")
try:
    res = bt.run_backtest(strategy="hmm_regime")
    print(res["total_return_pct"])
except Exception as e:
    import traceback
    traceback.print_exc()
