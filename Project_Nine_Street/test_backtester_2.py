import sys, os
sys.path.append(os.path.abspath('/Users/chuck/.openclaw/workspace/Project_Nine_Street'))
from ns_backtester import NSBacktester
bt = NSBacktester(tickers=['SPY'], start_date="2022-01-01")
res = bt.run_backtest(strategy='hmm_regime')
print(list(res['regimes']['SPY'].keys())[:5])
