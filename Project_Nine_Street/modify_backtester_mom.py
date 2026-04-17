import re

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/ns_backtester.py', 'r') as f:
    content = f.read()

old_code = """            # 2. Cross-Sectional Momentum Ranking (60-day)
            mom_60 = close_prices.pct_change(periods=60).fillna(0)
            ranks = mom_60.rank(axis=1, ascending=False)
            is_top = (ranks == 1.0).astype(float)

            # Normalize ties so max allocation sum is 1.0
            alloc = is_top.div(is_top.sum(axis=1).replace(0, 1), axis=0)"""

new_code = """            # 2. Blended Cross-Sectional Momentum Ranking (20-day & 60-day)
            mom_20 = close_prices.pct_change(periods=20).fillna(0)
            mom_60 = close_prices.pct_change(periods=60).fillna(0)
            
            # Blend 50/50 short and medium term momentum
            blended_mom = (mom_20 + mom_60) / 2
            ranks = blended_mom.rank(axis=1, ascending=False)
            
            # Top 2 Allocation (60% to #1, 40% to #2)
            alloc = pd.DataFrame(0.0, index=close_prices.index, columns=close_prices.columns)
            alloc[ranks == 1.0] = 0.60
            alloc[ranks == 2.0] = 0.40
            
            # Normalize just in case of ties
            alloc = alloc.div(alloc.sum(axis=1).replace(0, 1), axis=0)"""

content = content.replace(old_code, new_code)

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/ns_backtester.py', 'w') as f:
    f.write(content)
