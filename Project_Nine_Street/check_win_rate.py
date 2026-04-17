import sys, os
sys.path.append(os.path.abspath('/Users/chuck/.openclaw/workspace/Project_Nine_Street'))
from ns_backtester import NSBacktester

universe = ["SPY", "QQQ", "XLK", "XLE", "XLV", "XLF", "EFA", "EEM", "AGG", "TLT", "IEI", "DBC", "GLD"]
bt = NSBacktester(tickers=universe, start_date="2022-01-01")
res = bt.run_backtest(strategy='hmm_regime', target_vol=0.15)
print(f"Win Rate: {res['win_rate_pct']:.2f}%")
print(f"Total Trades: {res['total_trades']}")
