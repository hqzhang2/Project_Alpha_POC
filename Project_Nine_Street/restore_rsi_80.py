import re

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/ns_backtester.py', 'r') as f:
    content = f.read()

old_rsi = """                # Take profits aggressively to lock in wins
                take_profit = rsi > 70"""

new_rsi = """                # Take profits aggressively to lock in wins
                take_profit = rsi > 80"""

content = content.replace(old_rsi, new_rsi)

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/ns_backtester.py', 'w') as f:
    f.write(content)
