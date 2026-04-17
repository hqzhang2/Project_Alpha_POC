import re

# Update test_backtest_full.py
with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/test_backtest_full.py', 'r') as f:
    tb_content = f.read()

old_uni = "['SPY', 'QQQ', 'XLK', 'XLE', 'XLV', 'XLF', 'EFA', 'EEM', 'AGG', 'TLT', 'IEI', 'DBC', 'GLD']"
new_uni = "['SPY', 'QQQ', 'XLK', 'XLE', 'XLV', 'XLF', 'XLI', 'XLB', 'XLY', 'XLP', 'XLU', 'XLRE', 'XLC', 'EFA', 'EEM', 'AGG', 'TLT', 'IEI', 'DBC', 'GLD']"
tb_content = tb_content.replace(old_uni, new_uni)

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/test_backtest_full.py', 'w') as f:
    f.write(tb_content)

# Update NS_QA/server.py
with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/NS_QA/server.py', 'r') as f:
    server_content = f.read()

old_uni_str = 'universe = ["SPY", "QQQ", "XLK", "XLE", "XLV", "XLF", "EFA", "EEM", "AGG", "TLT", "IEI", "DBC", "GLD"]'
new_uni_str = 'universe = ["SPY", "QQQ", "XLK", "XLE", "XLV", "XLF", "XLI", "XLB", "XLY", "XLP", "XLU", "XLRE", "XLC", "EFA", "EEM", "AGG", "TLT", "IEI", "DBC", "GLD"]'
server_content = server_content.replace(old_uni_str, new_uni_str)

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/NS_QA/server.py', 'w') as f:
    f.write(server_content)
