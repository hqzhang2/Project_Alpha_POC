import re

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/ns_backtester.py', 'r') as f:
    content = f.read()

old_cs = """            if 'credit_spread_ratio' in features.columns:
                # Get Credit Spread Mom aligned with prices
                cs_mom = features.drop_duplicates(subset=['date']).set_index('date')['credit_spread_mom'].reindex(close_prices.index).ffill().fillna(0)
                # If credit spreads are widening rapidly (mom > 0.05 / 5%), force cash (Bear)
                cs_bear_mask = cs_mom > 0.05
                for col in regime_multiplier.columns:
                    regime_multiplier.loc[cs_bear_mask, col] = 0.0"""

new_cs = """            if 'credit_spread_ratio' in features.columns:
                # Get Credit Spread Mom aligned with prices
                cs_mom = features.drop_duplicates(subset=['date']).set_index('date')['credit_spread_mom'].reindex(close_prices.index).ffill().fillna(0)
                # If credit spreads are widening rapidly (ratio drops -> negative momentum < -0.05), force cash (Bear)
                cs_bear_mask = cs_mom < -0.05
                for col in regime_multiplier.columns:
                    regime_multiplier.loc[cs_bear_mask, col] = 0.0"""

content = content.replace(old_cs, new_cs)

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/ns_backtester.py', 'w') as f:
    f.write(content)
