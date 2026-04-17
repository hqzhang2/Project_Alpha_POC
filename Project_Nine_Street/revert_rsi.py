import re

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/ns_backtester.py', 'r') as f:
    content = f.read()

old_rsi = """            # --- High Win-Rate Mean Reversion Filter ---
            # To achieve > 75% win rate, we avoid buying at the top of the trend.
            # We only allocate if the asset has recently pulled back (RSI < 65)
            # and we aggressively take profits on spikes (RSI > 75).
            if 'RSI' in features.columns:
                rsi = features.pivot(index='date', columns='Ticker', values='RSI').reindex(close_prices.index).ffill().fillna(50)
                
                # Take profits aggressively to lock in wins
                take_profit = rsi > 70
                
                for col in is_top.columns:
                    if col in take_profit.columns:
                        is_top.loc[take_profit[col], col] = 0.0
                        
            row_sums = is_top.sum(axis=1).replace(0, 1)
            alloc = is_top.copy()
            alloc[row_sums > 1] = alloc[row_sums > 1].div(row_sums[row_sums > 1], axis=0)"""

new_rsi = """            # Normalize ties so max allocation sum is 1.0
            alloc = is_top.div(is_top.sum(axis=1).replace(0, 1), axis=0)"""

content = content.replace(old_rsi, new_rsi)

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/ns_backtester.py', 'w') as f:
    f.write(content)
