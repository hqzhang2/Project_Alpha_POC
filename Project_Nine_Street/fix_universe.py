import re

full_uni = '["SPY", "QQQ", "XLK", "XLE", "XLV", "XLF", "XLI", "XLB", "XLY", "XLP", "XLU", "XLRE", "XLC", "EFA", "EEM", "AGG", "TLT", "IEI", "DBC", "GLD"]'

# Fix server.py
with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/NS_QA/server.py', 'r') as f:
    server_content = f.read()

# Replace any universe = [...] with the full one
server_content = re.sub(r'universe\s*=\s*\[.*?\]', f'universe = {full_uni}', server_content)

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/NS_QA/server.py', 'w') as f:
    f.write(server_content)

# Fix test_backtest_full.py
with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/test_backtest_full.py', 'r') as f:
    tb_content = f.read()

tb_content = re.sub(r'universe\s*=\s*\[.*?\]', f'universe = {full_uni}', tb_content)

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/test_backtest_full.py', 'w') as f:
    f.write(tb_content)
