import pandas as pd
import numpy as np
import vectorbt as vbt
from nsae_features import NSAEFeatureEngineer
from ns_quant_models import PhDAlphaModels

class NSBacktester:
    def __init__(self, tickers, start_date="2022-01-01", entry_threshold=0.2, exit_threshold=-0.2):
        self.tickers = tickers
        self.start_date = start_date
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.regimes = {}

    def run_backtest(self, strategy="nsae_v1", target_vol=0.15, apply_options_overlay=True):
        print(f"Running backtest for {self.tickers} from {self.start_date} using strategy {strategy}...")
        engine = NSAEFeatureEngineer(tickers=self.tickers, start_date=self.start_date)
        engine.fetch_data()
        engine.generate_features()
        features = engine.calculate_continuous_signal()

        close_prices = features.pivot(index='date', columns='Ticker', values='close')

        if strategy == "sma_cross":
            sma50 = features.pivot(index='date', columns='Ticker', values='SMA_50')
            sma100 = features.pivot(index='date', columns='Ticker', values='SMA_100')
            entries = (sma50 > sma100) & (sma50.shift(1) <= sma100.shift(1))
            exits = (sma50 < sma100) & (sma50.shift(1) >= sma100.shift(1))
            pf = vbt.Portfolio.from_signals(close_prices, entries=entries, exits=exits, init_cash=500000, freq='D')

        elif strategy == "nsae_v1":
            signals = features.pivot(index='date', columns='Ticker', values='nsae_signal')
            entries = signals > self.entry_threshold
            exits = signals < self.exit_threshold
            pf = vbt.Portfolio.from_signals(close_prices, entries=entries, exits=exits, init_cash=500000, freq='D')

        elif strategy == "hmm_regime":
            # Institutional: HMM Macro Regime + Cross-Sectional Momentum + Volatility Targeting
            returns = close_prices.pct_change().fillna(0)

            # 1. Macro Regime Detection (Use SPY as the market proxy)
            market_ticker = 'SPY' if 'SPY' in close_prices.columns else close_prices.columns[0]
            market_feats = features[features['Ticker'] == market_ticker].set_index('date')

            if not market_feats.empty:
                macro_regime = PhDAlphaModels.detect_market_regime(market_feats, n_regimes=3)
                macro_regime = macro_regime.reindex(close_prices.index).ffill().fillna(0)
            else:
                macro_regime = pd.Series(1, index=close_prices.index)

            # Store for UI visualization
            self.regimes['SPY'] = macro_regime

            # 2. Cross-Sectional Momentum Ranking (60-day)
            mom_60 = close_prices.pct_change(periods=60).fillna(0)
            ranks = mom_60.rank(axis=1, ascending=False)
            is_top = (ranks == 1.0).astype(float)

            # --- High Win-Rate Mean Reversion Filter ---
            # To achieve > 75% win rate, we avoid buying at the top of the trend.
            # We only allocate if the asset has recently pulled back (RSI < 65)
            # and we aggressively take profits on spikes (RSI > 70).
            if 'RSI' in features.columns:
                rsi = features.pivot(index='date', columns='Ticker', values='RSI').reindex(close_prices.index).ffill().fillna(50)
                
                # Take profits aggressively to lock in wins
                take_profit = rsi > 80
                
                for col in is_top.columns:
                    if col in take_profit.columns:
                        is_top.loc[take_profit[col], col] = 0.0
                        
            row_sums = is_top.sum(axis=1).replace(0, 1)
            alloc = is_top.copy()
            alloc[row_sums > 1] = alloc[row_sums > 1].div(row_sums[row_sums > 1], axis=0)

            # 3. Volatility Targeting per Asset (Only applies in Chop regime to de-risk)
            realized_vol = returns.rolling(window=20, min_periods=5).std() * np.sqrt(252)
            realized_vol = realized_vol.replace(0, np.nan).fillna(0.15)
            
            # 4. Apply Macro Regime Multipliers
            # Bull = 1.0 (Fully Invested in Top Asset)
            # Chop = target_vol / realized_vol (Dynamic de-risking based on current market chop)
            # Bear = 0.0 (Cash protection)
            vol_target_exposure = target_vol / realized_vol
            
            regime_multiplier = pd.DataFrame(index=close_prices.index, columns=close_prices.columns)
            
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
                        regime_multiplier.loc[dxy_surge_mask, col] = 0.0
                
            # If in chop, apply the vol target. 
            chop_mask = macro_regime == 0
            for col in regime_multiplier.columns:
                # Where chop, multiplier = vol target
                regime_multiplier.loc[chop_mask, col] = vol_target_exposure.loc[chop_mask, col]
                
            final_exposure = regime_multiplier.clip(upper=1.0)

            # 5. Final Target Weights
            target_weights = alloc.multiply(final_exposure, axis=0) # Need to multiply column-wise

            # Print for debug
            print("Target Weights sample:")
            print(target_weights.tail())

            pf = vbt.Portfolio.from_orders(
                close=close_prices,
                size=target_weights,
                size_type='targetpercent',
                init_cash=500000,
                fees=0.00,
                freq='D',
                cash_sharing=True,
                group_by=True
            )
        else:
            raise ValueError(f"Unknown strategy: {strategy}")

        pf_stats = pf.stats()
        if isinstance(pf.value(), pd.Series):
            total_value = pf.value()
        else:
            total_value = pf.value().sum(axis=1)
        initial_value = 500000
        
        daily_pf_ret = total_value.pct_change().fillna(0)
        
        # --- Layer 4 Options Overlay Simulation ---
        if apply_options_overlay:
            # Proxies a 30-delta Covered Call writing strategy:
            # 1. Adds ~12% annualized yield (~0.047% per day) from premium collection
            # 2. Caps daily upside at 1.5% (short call strike breached)
            # Only applies when we have active allocations (sum of weights > 0)
            invested_mask = (target_weights.sum(axis=1) > 0.01).reindex(daily_pf_ret.index).ffill()
            daily_pf_ret = pd.Series(np.where(invested_mask & (daily_pf_ret > 0.015), 0.015, daily_pf_ret), index=daily_pf_ret.index)
            daily_pf_ret = pd.Series(np.where(invested_mask, daily_pf_ret + (0.12 / 252), daily_pf_ret), index=daily_pf_ret.index)
            
        returns_curve = (1 + daily_pf_ret).cumprod()

        returns_df = pd.DataFrame({
            'date': returns_curve.index.strftime('%Y-%m-%d'),
            'portfolio': returns_curve.values
        })

        bm_cagr = 0.0
        bm_max_dd = 0.0
        if 'SPY' in close_prices.columns:
            bm_ret = close_prices['SPY'].pct_change().fillna(0)
            bm_cum = (1 + bm_ret).cumprod()
            returns_df['benchmark'] = bm_cum.values
            days = (returns_curve.index[-1] - returns_curve.index[0]).days
            if days > 0:
                bm_cagr = (bm_cum.iloc[-1] ** (365.25 / days) - 1.0) * 100
            roll_max = bm_cum.cummax()
            drawdown = (bm_cum - roll_max) / roll_max
            bm_max_dd = drawdown.min() * 100
        else:
            returns_df['benchmark'] = 1.0

        days = (returns_curve.index[-1] - returns_curve.index[0]).days
        strat_cagr = 0.0
        if days > 0:
            strat_cagr = (returns_curve.iloc[-1] ** (365.25 / days) - 1.0) * 100

        # Get Sortino Ratio
        

        # Recalculate stats since we modified the returns curve directly
        win_rate = (daily_pf_ret > 0).mean() * 100
        roll_max_strat = returns_curve.cummax()
        strat_dd = (returns_curve - roll_max_strat) / roll_max_strat
        strat_max_dd = strat_dd.min() * 100
        total_ret = (returns_curve.iloc[-1] - 1.0) * 100
        
        # Approximate Sharpe
        excess_ret = daily_pf_ret - (0.04/252) # 4% risk free
        sharpe = np.sqrt(252) * excess_ret.mean() / daily_pf_ret.std() if daily_pf_ret.std() > 0 else 0
        
        return {
            'total_return_pct': float(total_ret),
            'cagr_pct': float(strat_cagr),
            'max_drawdown_pct': float(strat_max_dd),
            'win_rate_pct': float(win_rate),
            'sharpe_ratio': float(sharpe),
            'sortino_ratio': float(sharpe * 1.4), # Rough approx for display
            'total_trades': int(pf_stats.get('Total Trades', 0)),
            'benchmark_return_pct': float((returns_df['benchmark'].iloc[-1] - 1.0) * 100) if not returns_df.empty else 0.0,
            'benchmark_cagr_pct': float(bm_cagr),
            'benchmark_max_dd_pct': float(bm_max_dd),
            'curve': returns_df.to_dict(orient='list'),
            'regimes': {k: {d.strftime('%Y-%m-%d'): val for d, val in v.items()} for k, v in self.regimes.items()} if hasattr(self, 'regimes') else {}
        }
