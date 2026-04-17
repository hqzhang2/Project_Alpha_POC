import re

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/ns_backtester.py', 'r') as f:
    content = f.read()

old_stats = """        if isinstance(pf.value(), pd.Series):"""
new_stats = """        pf_stats = pf.stats()
        if isinstance(pf.value(), pd.Series):"""

content = content.replace(old_stats, new_stats)

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/ns_backtester.py', 'w') as f:
    f.write(content)
