import pandas as pd
import numpy as np
import scipy.stats as si
from hmmlearn.hmm import GMMHMM
import statsmodels.tsa.stattools as ts
import ruptures as rpt

class PhDOptionModels:
    """Institutional Options Math: Probability of Profit, VRP, and Expected Value"""
    
    @staticmethod
    def calc_pop(S, K, T, r, sigma, premium, option_type='p'):
        if T <= 0 or sigma <= 0: return 0.0
        be = K - premium if option_type == 'p' else K + premium
        d2 = (np.log(S / be) + (r - 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
        if option_type == 'p':
            pop = si.norm.cdf(d2)
        else:
            pop = si.norm.cdf(-d2)
        return pop * 100.0

class PhDAlphaModels:
    """Institutional Alpha Math: HMM Regime Detection and Cointegration"""
    
    @staticmethod
    def detect_market_regime(features_df: pd.DataFrame, n_regimes=3):
        """
        Uses GMMHMM to detect market regimes (e.g. Bull, Bear, Chop) 
        from a 5-factor feature vector.
        Features expected: ['ADX', 'RSI', 'BB_width', 'vol_ratio', 'OBV_slope']
        """
        # Ensure we have the 5 factors
        factors = ['ADX', 'RSI', 'BB_width', 'vol_ratio', 'OBV_slope']
        X = features_df[factors].dropna().values
        
        if len(X) < 50:
            return pd.Series(0, index=features_df.index)
            
        # Train GMMHMM
        hmm = GMMHMM(n_components=n_regimes, n_mix=2, covariance_type="diag", n_iter=1000, random_state=42)
        hmm.fit(X)
        
        hidden_states = hmm.predict(X)
        
        # Map latent states to human-readable regimes: Bull, Bear, Chop
        # We can use RSI or OBV_slope mean to determine the state mapping
        state_means = []
        for i in range(n_regimes):
            state_data = X[hidden_states == i]
            # Assuming RSI is index 1, OBV_slope is index 4
            if len(state_data) > 0:
                rsi_mean = np.mean(state_data[:, 1])
                state_means.append((i, rsi_mean))
            else:
                state_means.append((i, 0))
                
        # Sort states by RSI: Highest RSI -> Bull, Lowest -> Bear, Middle -> Chop
        state_means.sort(key=lambda x: x[1])
        
        if n_regimes == 3:
            bear_state = state_means[0][0]
            chop_state = state_means[1][0]
            bull_state = state_means[2][0]
            
            regime_map = {
                bear_state: -1, # Bear
                chop_state: 0,  # Chop
                bull_state: 1   # Bull
            }
        else:
            # Fallback for 2 regimes
            regime_map = {
                state_means[0][0]: -1,
                state_means[1][0]: 1
            }
            
        regime_signals = np.array([regime_map[state] for state in hidden_states])
        
        # Integrate ruptures for change-point detection on returns (using close if available, else first factor)
        # Let's run a Pelt search to find structural breaks
        algo = rpt.Pelt(model="rbf").fit(X[:, 1]) # using RSI for break detection
        result = algo.predict(pen=10)
        
        # We could use the breakpoints to adjust regimes, but for now we'll just return regimes
        # and store breakpoints if needed.
        
        regimes_series = pd.Series(regime_signals, index=features_df[factors].dropna().index)
        return regimes_series.reindex(features_df.index).ffill().fillna(0)

    @staticmethod
    def calculate_cointegration(series1: pd.Series, series2: pd.Series):
        df = pd.concat([series1, series2], axis=1).dropna()
        if len(df) < 50: return False, 0.0
        s1 = df.iloc[:, 0]
        s2 = df.iloc[:, 1]
        score, pvalue, _ = ts.coint(s1, s2)
        return pvalue < 0.05, pvalue
