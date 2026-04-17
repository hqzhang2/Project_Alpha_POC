import re

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/ns_backtester.py', 'r') as f:
    content = f.read()

# Add VIX capitulation override
# Find macro_regime mapping
old_code = """
            # 4. Apply Macro Regime Multipliers
            # Bull = 1.0 (Fully Invested in Top Asset)
            # Chop = target_vol / realized_vol (Dynamic de-risking based on current market chop)
            # Bear = 0.0 (Cash protection)
            vol_target_exposure = target_vol / realized_vol
            
            regime_multiplier = pd.DataFrame(index=close_prices.index, columns=close_prices.columns)
            for col in regime_multiplier.columns:
                regime_multiplier[col] = macro_regime.map({
                    1: 1.0, 
                    0: 0.5, # We'll use a strict 50% cut in chop, or we can use vol_target_exposure
                    -1: 0.0
                }).fillna(0)
"""

new_code = """
            # 4. Apply Macro Regime Multipliers
            # Bull = 1.0 (Fully Invested in Top Asset)
            # Chop = target_vol / realized_vol (Dynamic de-risking based on current market chop)
            # Bear = 0.0 (Cash protection)
            vol_target_exposure = target_vol / realized_vol
            
            regime_multiplier = pd.DataFrame(index=close_prices.index, columns=close_prices.columns)
            for col in regime_multiplier.columns:
                regime_multiplier[col] = macro_regime.map({
                    1: 1.0, 
                    0: 0.5, # We'll use a strict 50% cut in chop, or we can use vol_target_exposure
                    -1: 0.0
                }).fillna(0)
                
            # --- VIX Capitulation & Credit Spread Override ---
            if 'vix_capitulation' in features.columns:
                # Get VIX cap signal aligned with prices
                vix_cap = features.drop_duplicates(subset=['date']).set_index('date')['vix_capitulation'].reindex(close_prices.index).ffill().fillna(0)
                # If VIX capitulation is 1, force full Bull exposure to catch the bottom bounce
                cap_mask = vix_cap == 1
                for col in regime_multiplier.columns:
                    regime_multiplier.loc[cap_mask, col] = 1.0
                    
            if 'credit_spread_ratio' in features.columns:
                # Get Credit Spread Mom aligned with prices
                cs_mom = features.drop_duplicates(subset=['date']).set_index('date')['credit_spread_mom'].reindex(close_prices.index).ffill().fillna(0)
                # If credit spreads are widening rapidly (mom > 0.05 / 5%), force cash (Bear)
                cs_bear_mask = cs_mom > 0.05
                for col in regime_multiplier.columns:
                    regime_multiplier.loc[cs_bear_mask, col] = 0.0
"""

content = content.replace(old_code, new_code)

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/ns_backtester.py', 'w') as f:
    f.write(content)
