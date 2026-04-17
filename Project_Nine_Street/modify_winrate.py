import re

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/ns_backtester.py', 'r') as f:
    content = f.read()

# Let's add an RSI overbought filter to take profits and increase win rate.
old_alloc = """            # 2. Cross-Sectional Momentum Ranking (60-day)
            mom_60 = close_prices.pct_change(periods=60).fillna(0)
            ranks = mom_60.rank(axis=1, ascending=False)
            is_top = (ranks == 1.0).astype(float)

            # Normalize ties so max allocation sum is 1.0
            alloc = is_top.div(is_top.sum(axis=1).replace(0, 1), axis=0)"""

new_alloc = """            # 2. Cross-Sectional Momentum Ranking (60-day)
            mom_60 = close_prices.pct_change(periods=60).fillna(0)
            ranks = mom_60.rank(axis=1, ascending=False)
            is_top = (ranks == 1.0).astype(float)

            # --- Take Profit / Overbought Filter for Higher Win Rate ---
            # If the #1 asset is severely overbought (RSI > 75), we take profits by cutting allocation
            # and rotating to cash for that portion, locking in the win.
            # We need RSI. Fortunately, it's in features.
            if 'RSI' in features.columns:
                rsi = features.pivot(index='date', columns='Ticker', values='RSI').reindex(close_prices.index).ffill().fillna(50)
                # Where RSI > 75, reduce exposure by 50% to take profits.
                # Where RSI > 80, reduce exposure to 0.
                overbought_75 = rsi > 75
                overbought_80 = rsi > 80
                
                # Apply filter to is_top
                for col in is_top.columns:
                    if col in overbought_80.columns:
                        is_top.loc[overbought_80[col], col] = 0.0
                        # For >75 but <=80, cut to 0.5
                        mask_75 = overbought_75[col] & ~overbought_80[col]
                        is_top.loc[mask_75, col] = 0.5

            # Normalize ties so max allocation sum is 1.0 (but keep 0.5 if it was cut)
            # Actually, if we cut to 0.5, we want the total allocation to be 0.5 (rest in cash)
            # So we don't normalize to 1 if the sum is < 1.
            row_sums = is_top.sum(axis=1).replace(0, 1)
            alloc = is_top.copy()
            # Only normalize rows where sum > 1 (e.g. ties for #1)
            alloc[row_sums > 1] = alloc[row_sums > 1].div(row_sums[row_sums > 1], axis=0)"""

content = content.replace(old_alloc, new_alloc)

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/ns_backtester.py', 'w') as f:
    f.write(content)
