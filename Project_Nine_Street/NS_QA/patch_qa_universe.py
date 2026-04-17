import re

# Update server.py
with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/NS_QA/server.py', 'r') as f:
    content = f.read()

old_uni = 'universe = ["SPY", "QQQ", "XLK", "XLE", "XLV", "XLF"]'
new_uni = 'universe = ["SPY", "QQQ", "XLK", "XLE", "XLV", "XLF", "EFA", "EEM", "AGG", "TLT", "IEI", "DBC", "GLD"]'

content = content.replace(old_uni, new_uni)

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/NS_QA/server.py', 'w') as f:
    f.write(content)

# Update index.html
with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/NS_QA/index.html', 'r') as f:
    html = f.read()

html = html.replace('<option value="hmm_regime">Ph.D. HMM Regime</option>', '<option value="hmm_regime">NS-Regime-1 (Global Macro)</option>')

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/NS_QA/index.html', 'w') as f:
    f.write(html)

