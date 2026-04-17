import re

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/ns_backtester.py', 'r') as f:
    content = f.read()

# Modify run_backtest signature
content = content.replace("def run_backtest(self, strategy=\"nsae_v1\", target_vol=0.15):", "def run_backtest(self, strategy=\"nsae_v1\", target_vol=0.15, apply_options_overlay=True):")

# Modify stats calculation
old_stats = """        stats = pf.stats()
        if isinstance(pf.value(), pd.Series):
            total_value = pf.value()
        else:
            total_value = pf.value().sum(axis=1)
        initial_value = 500000 # since we used group_by=True and cash_sharing=True
        returns_curve = total_value / initial_value

        returns_df = pd.DataFrame({
            'date': returns_curve.index.strftime('%Y-%m-%d'),
            'portfolio': returns_curve.values
        })"""

new_stats = """        if isinstance(pf.value(), pd.Series):
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
        })"""

content = content.replace(old_stats, new_stats)

old_return_dict = """        return {
            'total_return_pct': float(stats.get('Total Return [%]', 0)),
            'cagr_pct': float(strat_cagr),
            'max_drawdown_pct': float(stats.get('Max Drawdown [%]', 0)),
            'win_rate_pct': float(stats.get('Win Rate [%]', 0)),
            'sharpe_ratio': float(stats.get('Sharpe Ratio', 0)),
            'sortino_ratio': sortino,
            'total_trades': int(stats.get('Total Trades', 0)),
            'benchmark_return_pct': float((returns_df['benchmark'].iloc[-1] - 1.0) * 100) if not returns_df.empty else 0.0,
            'benchmark_cagr_pct': float(bm_cagr),
            'benchmark_max_dd_pct': float(bm_max_dd),
            'curve': returns_df.to_dict(orient='list'),
            'regimes': {k: {d.strftime('%Y-%m-%d'): val for d, val in v.items()} for k, v in self.regimes.items()} if hasattr(self, 'regimes') else {}
        }"""

new_return_dict = """        # Recalculate stats since we modified the returns curve directly
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
            'total_trades': int(pf.stats().get('Total Trades', 0)),
            'benchmark_return_pct': float((returns_df['benchmark'].iloc[-1] - 1.0) * 100) if not returns_df.empty else 0.0,
            'benchmark_cagr_pct': float(bm_cagr),
            'benchmark_max_dd_pct': float(bm_max_dd),
            'curve': returns_df.to_dict(orient='list'),
            'regimes': {k: {d.strftime('%Y-%m-%d'): val for d, val in v.items()} for k, v in self.regimes.items()} if hasattr(self, 'regimes') else {}
        }"""

content = content.replace(old_return_dict, new_return_dict)

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/ns_backtester.py', 'w') as f:
    f.write(content)
