import re

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/ns_backtester.py', 'r') as f:
    content = f.read()

content = content.replace("sortino = float(stats.get('Sortino Ratio', 0))", "")
content = content.replace("stats = pf.stats()", "pf_stats = pf.stats()")
content = content.replace("int(pf.stats().get('Total Trades', 0))", "int(pf_stats.get('Total Trades', 0))")

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/ns_backtester.py', 'w') as f:
    f.write(content)
