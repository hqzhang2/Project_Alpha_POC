import sys, os
sys.path.append(os.path.abspath('/Users/chuck/.openclaw/workspace/Project_Nine_Street'))
from ns_backtester import NSBacktester
universe = ["SPY", "QQQ", "XLK", "XLE", "XLV", "XLF"]
bt = NSBacktester(tickers=universe, start_date="2022-01-01")
res = bt.run_backtest(strategy='hmm_regime', target_vol=0.15)
