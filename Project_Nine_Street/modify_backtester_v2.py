import re

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/ns_backtester.py', 'r') as f:
    content = f.read()

# Replace Top 2 allocation with Top 1
old_mom = """            # Top 2 Allocation (60% to #1, 40% to #2)
            alloc = pd.DataFrame(0.0, index=close_prices.index, columns=close_prices.columns)
            alloc[ranks == 1.0] = 0.60
            alloc[ranks == 2.0] = 0.40
            
            # Normalize just in case of ties
            alloc = alloc.div(alloc.sum(axis=1).replace(0, 1), axis=0)"""

new_mom = """            # Winner Takes All Allocation (100% to #1)
            is_top = (ranks == 1.0).astype(float)
            
            # Normalize just in case of ties
            alloc = is_top.div(is_top.sum(axis=1).replace(0, 1), axis=0)"""

content = content.replace(old_mom, new_mom)

# Replace VIX Capitulation logic
old_vix = """            # --- VIX Capitulation & Credit Spread Override ---
            if 'vix_capitulation' in features.columns:
                # Get VIX cap signal aligned with prices
                vix_cap = features.drop_duplicates(subset=['date']).set_index('date')['vix_capitulation'].reindex(close_prices.index).ffill().fillna(0)
                # If VIX capitulation is 1, force full Bull exposure to catch the bottom bounce
                cap_mask = vix_cap == 1
                for col in regime_multiplier.columns:
                    regime_multiplier.loc[cap_mask, col] = 1.0"""

new_vix = """            # --- VIX Capitulation & Credit Spread Override ---
            if 'vix_capitulation' in features.columns:
                # Get VIX cap signal aligned with prices
                vix_cap = features.drop_duplicates(subset=['date']).set_index('date')['vix_capitulation'].reindex(close_prices.index).ffill().fillna(0)
                
                # Price Confirmation: price must be above its 5-day SMA to avoid falling knives
                sma_5 = close_prices.rolling(window=5, min_periods=1).mean()
                price_confirmed = close_prices > sma_5
                
                # If VIX capitulation is 1 AND price is confirmed, force full Bull exposure
                for col in regime_multiplier.columns:
                    cap_confirmed_mask = (vix_cap == 1) & price_confirmed[col]
                    regime_multiplier.loc[cap_confirmed_mask, col] = 1.0"""

content = content.replace(old_vix, new_vix)

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/ns_backtester.py', 'w') as f:
    f.write(content)
