import re

# 1. Update test_backtest_full.py
with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/test_backtest_full.py', 'r') as f:
    tb_content = f.read()
tb_content = tb_content.replace(
    'universe = ["SPY", "QQQ", "XLK", "XLE", "XLV", "XLF"]', 
    'universe = ["SPY", "QQQ", "XLK", "XLE", "XLV", "XLF", "EFA", "EEM", "AGG", "TLT", "IEI", "DBC", "GLD"]'
)
with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/test_backtest_full.py', 'w') as f:
    f.write(tb_content)


# 2. Update nsae_features.py
with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/nsae_features.py', 'r') as f:
    nf_content = f.read()

# Replace macro tickers and logic
old_macro = """        # Fetch Macro data
        macro_tickers = ['^VIX', 'HYG', 'TLT']
        macro_dfs = []
        for mt in macro_tickers:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                mdf = yf.download(mt, start=self.start_date, end=self.end_date, progress=False)
            if not mdf.empty:
                if isinstance(mdf.columns, pd.MultiIndex):
                    mdf.columns = mdf.columns.droplevel(1)
                mdf['Ticker'] = mt
                macro_dfs.append(mdf)
                
        if macro_dfs:
            md = pd.concat(macro_dfs).reset_index()
            md.rename(columns={'Date': 'date', 'Close': 'close'}, inplace=True)
            self.macro_data = md.pivot(index='date', columns='Ticker', values='close').ffill()
            
            # Calculate Macro Features
            if '^VIX' in self.macro_data.columns:
                vix = self.macro_data['^VIX']
                # VIX Capitulation Signal: VIX spikes > 30 OR VIX is > 2.5 standard deviations above its 50-day moving average
                vix_sma = vix.rolling(50).mean()
                vix_std = vix.rolling(50).std()
                self.macro_data['VIX_zscore'] = (vix - vix_sma) / vix_std
                self.macro_data['vix_capitulation'] = ((vix > 30) | (self.macro_data['VIX_zscore'] > 2.5)).astype(int)
            
            if 'HYG' in self.macro_data.columns and 'TLT' in self.macro_data.columns:
                # Credit Spread proxy: HYG (Junk) / TLT (Treasury) ratio
                # If ratio drops significantly, credit spreads are blowing out (Bearish)
                self.macro_data['credit_spread_ratio'] = self.macro_data['HYG'] / self.macro_data['TLT']
                self.macro_data['credit_spread_mom'] = self.macro_data['credit_spread_ratio'].pct_change(20) # 1 month momentum"""

new_macro = """        # Fetch Macro data
        macro_tickers = ['HYG', 'TLT', 'DX-Y.NYB'] # DX-Y.NYB is the US Dollar Index
        macro_dfs = []
        for mt in macro_tickers:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                mdf = yf.download(mt, start=self.start_date, end=self.end_date, progress=False)
            if not mdf.empty:
                if isinstance(mdf.columns, pd.MultiIndex):
                    mdf.columns = mdf.columns.droplevel(1)
                mdf['Ticker'] = mt
                macro_dfs.append(mdf)
                
        if macro_dfs:
            md = pd.concat(macro_dfs).reset_index()
            md.rename(columns={'Date': 'date', 'Close': 'close'}, inplace=True)
            self.macro_data = md.pivot(index='date', columns='Ticker', values='close').ffill()
            
            # Calculate Macro Features
            if 'DX-Y.NYB' in self.macro_data.columns:
                self.macro_data['dxy_mom'] = self.macro_data['DX-Y.NYB'].pct_change(20) # 1 month dollar momentum
            
            if 'HYG' in self.macro_data.columns and 'TLT' in self.macro_data.columns:
                self.macro_data['credit_spread_ratio'] = self.macro_data['HYG'] / self.macro_data['TLT']
                self.macro_data['credit_spread_mom'] = self.macro_data['credit_spread_ratio'].pct_change(20)"""

nf_content = nf_content.replace(old_macro, new_macro)
with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/nsae_features.py', 'w') as f:
    f.write(nf_content)


# 3. Update ns_backtester.py
with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/ns_backtester.py', 'r') as f:
    nb_content = f.read()

# Fix regime multiplier to allow safe havens + Add DXY
old_regime = """            regime_multiplier = pd.DataFrame(index=close_prices.index, columns=close_prices.columns)
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
                
                # Price Confirmation: price must be above its 5-day SMA to avoid falling knives
                sma_5 = close_prices.rolling(window=5, min_periods=1).mean()
                price_confirmed = close_prices > sma_5
                
                # If VIX capitulation is 1 AND price is confirmed, force full Bull exposure
                for col in regime_multiplier.columns:
                    cap_confirmed_mask = (vix_cap == 1) & price_confirmed[col]
                    regime_multiplier.loc[cap_confirmed_mask, col] = 1.0
                    
            if 'credit_spread_ratio' in features.columns:
                # Get Credit Spread Mom aligned with prices
                cs_mom = features.drop_duplicates(subset=['date']).set_index('date')['credit_spread_mom'].reindex(close_prices.index).ffill().fillna(0)
                # If credit spreads are widening rapidly (ratio drops -> negative momentum < -0.05), force cash (Bear)
                cs_bear_mask = cs_mom < -0.05
                for col in regime_multiplier.columns:
                    regime_multiplier.loc[cs_bear_mask, col] = 0.0"""

new_regime = """            regime_multiplier = pd.DataFrame(index=close_prices.index, columns=close_prices.columns)
            
            # Global Macro Rotation: When SPY is in a bear market (0.0), we still allow full allocation 
            # to "Safe Haven" assets if they have the best momentum.
            safe_havens = ['AGG', 'TLT', 'IEI', 'GLD']
            international_and_commodities = ['EFA', 'EEM', 'DBC']
            
            for col in regime_multiplier.columns:
                if col in safe_havens:
                    regime_multiplier[col] = 1.0 # Always allow Safe Havens
                else:
                    regime_multiplier[col] = macro_regime.map({
                        1: 1.0, 
                        0: 0.5, 
                        -1: 0.0
                    }).fillna(0)
                    
            # --- Macro Overrides (Credit & DXY) ---
            if 'credit_spread_ratio' in features.columns:
                cs_mom = features.drop_duplicates(subset=['date']).set_index('date')['credit_spread_mom'].reindex(close_prices.index).ffill().fillna(0)
                # If credit spreads are widening rapidly, force cash for Risk-On assets
                cs_bear_mask = cs_mom < -0.05
                for col in regime_multiplier.columns:
                    if col not in safe_havens:
                        regime_multiplier.loc[cs_bear_mask, col] = 0.0
                        
            if 'dxy_mom' in features.columns:
                dxy_mom = features.drop_duplicates(subset=['date']).set_index('date')['dxy_mom'].reindex(close_prices.index).ffill().fillna(0)
                # DXY Wrecking Ball: If Dollar is surging > 3% in a month, it crushes Emerging Markets, Gold, and Commodities
                dxy_surge_mask = dxy_mom > 0.03
                for col in ['EEM', 'DBC', 'GLD', 'EFA']:
                    if col in regime_multiplier.columns:
                        regime_multiplier.loc[dxy_surge_mask, col] = 0.0"""

nb_content = nb_content.replace(old_regime, new_regime)
with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/ns_backtester.py', 'w') as f:
    f.write(nb_content)
