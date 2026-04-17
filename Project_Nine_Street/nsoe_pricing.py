import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
import py_vollib.black_scholes_merton.implied_volatility as iv
import py_vollib.black_scholes_merton.greeks.analytical as greeks

class NSOEOptionEngine:
    """
    Nine Street Option Engine (NSOE)
    Fetches option chains, calculates Implied Volatility (IV) and Greeks (Delta, Gamma, Theta, Vega)
    using the Black-Scholes-Merton model via py_vollib. Filters for target DTE and Delta.
    """
    
    def __init__(self, ticker: str, risk_free_rate: float = 0.045, div_yield: float = 0.015):
        self.ticker = ticker
        self.r = risk_free_rate
        self.q = div_yield
        self.tkr = yf.Ticker(ticker)
        self.underlying_price = None
        self.chain_data = pd.DataFrame()

    def fetch_options_chain(self, min_dte: int = 30, max_dte: int = 45):
        """Fetches call and put options within the target Days to Expiration (DTE) window."""
        print(f"Fetching {self.ticker} options data...")
        self.underlying_price = self.tkr.history(period="1d")['Close'].iloc[-1]
        
        expirations = self.tkr.options
        today = datetime.now()
        
        valid_expirations = []
        for exp in expirations:
            exp_date = datetime.strptime(exp, '%Y-%m-%d')
            dte = (exp_date - today).days
            if min_dte <= dte <= max_dte:
                valid_expirations.append((exp, dte))
                
        if not valid_expirations:
            print(f"No expirations found between {min_dte} and {max_dte} DTE.")
            return pd.DataFrame()
            
        print(f"Found expirations: {[e[0] for e in valid_expirations]}")
        
        all_options = []
        for exp, dte in valid_expirations:
            opt = self.tkr.option_chain(exp)
            
            calls = opt.calls.copy()
            calls['type'] = 'c'
            
            puts = opt.puts.copy()
            puts['type'] = 'p'
            
            chain = pd.concat([calls, puts])
            chain['expiration'] = exp
            chain['dte'] = dte
            chain['t'] = dte / 365.0 # Time to expiration in years
            all_options.append(chain)
            
        self.chain_data = pd.concat(all_options)
        self.chain_data = self.chain_data[['contractSymbol', 'type', 'strike', 'lastPrice', 'bid', 'ask', 'volume', 'openInterest', 'expiration', 'dte', 't']]
        return self.chain_data

    def calculate_greeks(self):
        """Calculates IV and Greeks using py_vollib"""
        print("Calculating IV and Greeks via Black-Scholes-Merton...")
        
        # We need a mid price for accurate implied volatility
        self.chain_data['mid_price'] = (self.chain_data['bid'] + self.chain_data['ask']) / 2.0
        # Fallback to lastPrice if bid/ask is dead
        self.chain_data['price_for_calc'] = np.where(self.chain_data['mid_price'] > 0, self.chain_data['mid_price'], self.chain_data['lastPrice'])
        
        results = []
        for _, row in self.chain_data.iterrows():
            S = self.underlying_price
            K = row['strike']
            t = row['t']
            r = self.r
            q = self.q
            price = row['price_for_calc']
            flag = row['type'] # 'c' or 'p'
            
            if price <= 0 or t <= 0:
                continue
                
            try:
                # Calculate IV
                implied_vol = iv.implied_volatility(price, S, K, t, r, q, flag)
                
                # Calculate Greeks
                delta = greeks.delta(flag, S, K, t, r, implied_vol, q)
                gamma = greeks.gamma(flag, S, K, t, r, implied_vol, q)
                theta = greeks.theta(flag, S, K, t, r, implied_vol, q)
                vega = greeks.vega(flag, S, K, t, r, implied_vol, q)
                
                results.append({
                    'contractSymbol': row['contractSymbol'],
                    'iv': implied_vol,
                    'delta': delta,
                    'gamma': gamma,
                    'theta': theta,
                    'vega': vega
                })
            except Exception as e:
                pass # Skip options where BSM fails to converge
                
        greeks_df = pd.DataFrame(results)
        if not greeks_df.empty:
            self.chain_data = self.chain_data.merge(greeks_df, on='contractSymbol')
        
        return self.chain_data
        
    def screen_premium_targets(self, target_delta_call: float = 0.30, target_delta_put: float = -0.30, tolerance: float = 0.05):
        """Filters the chain for standard premium selling targets (e.g. ~30 delta)."""
        print(f"\nScreening for Income Targets (~{abs(target_delta_call)*100:.0f} Delta)...")
        
        calls = self.chain_data[
            (self.chain_data['type'] == 'c') & 
            (self.chain_data['delta'] >= target_delta_call - tolerance) & 
            (self.chain_data['delta'] <= target_delta_call + tolerance)
        ]
        
        puts = self.chain_data[
            (self.chain_data['type'] == 'p') & 
            (self.chain_data['delta'] >= target_delta_put - tolerance) & 
            (self.chain_data['delta'] <= target_delta_put + tolerance)
        ]
        
        return pd.concat([calls, puts]).sort_values(by=['type', 'dte', 'strike'])

if __name__ == "__main__":
    # Test execution for Project Nine Street: SPY ~30-45 DTE Options
    engine = NSOEOptionEngine(ticker="SPY")
    
    chain = engine.fetch_options_chain(min_dte=30, max_dte=45)
    
    if not chain.empty:
        engine.calculate_greeks()
        targets = engine.screen_premium_targets(target_delta_call=0.30, target_delta_put=-0.30)
        
        print(f"\n--- Underlying Price: ${engine.underlying_price:.2f} ---")
        
        # Display selected columns for readability
        display_cols = ['type', 'expiration', 'dte', 'strike', 'mid_price', 'iv', 'delta', 'theta', 'volume']
        print(targets[display_cols].to_string(index=False))
