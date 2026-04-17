import sys, os
sys.path.append(os.path.abspath('/Users/chuck/.openclaw/workspace/Project_Nine_Street'))
from ns_backtester import NSBacktester
universe = ["SPY", "QQQ", "XLK", "XLE", "XLV", "XLF", "XLI", "XLB", "XLY", "XLP", "XLU", "XLRE", "XLC", "EFA", "EEM", "AGG", "TLT", "IEI", "DBC", "GLD"]
bt = NSBacktester(tickers=universe, start_date="2022-01-01")
res = bt.run_backtest(strategy='hmm_regime', target_vol=0.15)
print(f"Strat Return: {res['total_return_pct']:.2f}% | SPY Bench: {res['benchmark_return_pct']:.2f}%")
print(f"Strat MaxDD: {res['max_drawdown_pct']:.2f}% | SPY DD: {res['benchmark_max_dd_pct']:.2f}%")
print(f"Sharpe: {res['sharpe_ratio']:.2f} | Sortino: {res['sortino_ratio']:.2f}")
