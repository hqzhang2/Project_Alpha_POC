import json
import os
from nsoe_pricing import NSOEOptionEngine

def run_layer4_options_overlay():
    print("=== NS Layer 4: Options Overlay & Execution Engine ===")
    
    # 1. Read Current Paper Portfolio Position
    portfolio_path = '/Users/chuck/.openclaw/workspace/Project_Nine_Street/paper_portfolio.json'
    if not os.path.exists(portfolio_path):
        print("Error: Paper portfolio not found.")
        return
        
    with open(portfolio_path, 'r') as f:
        port = json.load(f)
        
    equities = port['positions'].get('equities', {})
    if not equities:
        print("No underlying equities to overlay.")
        return
        
    # Assume we overlay the largest position
    target_ticker = list(equities.keys())[0]
    shares = equities[target_ticker]['shares']
    print(f"Targeting largest holding: {target_ticker} ({shares} shares)")
    
    # We can sell 1 contract per 100 shares
    contracts_to_sell = shares // 100
    if contracts_to_sell < 1:
        print(f"Not enough shares of {target_ticker} to sell options (need 100, have {shares}).")
        return
        
    print(f"Capacity to sell: {contracts_to_sell} contracts")
    
    # 2. Spin up Options Engine
    # Target 30-45 DTE, ~30 Delta for Covered Calls (Standard VRP Harvest)
    engine = NSOEOptionEngine(ticker=target_ticker)
    chain = engine.fetch_options_chain(min_dte=30, max_dte=45)
    
    if chain.empty:
        print(f"Failed to fetch valid options chain for {target_ticker}.")
        return
        
    engine.calculate_greeks()
    
    # 3. Layer 3/4 Decision Logic: 
    # For now, default to Covered Calls on long equity positions. 
    targets = engine.screen_premium_targets(target_delta_call=0.30, target_delta_put=-0.30)
    
    # Get the best Call
    calls = targets[targets['type'] == 'c']
    if calls.empty:
        print("No valid Call targets found.")
        return
        
    # Select the call closest to 30 delta
    calls['delta_dist'] = abs(calls['delta'] - 0.30)
    best_call = calls.sort_values(by='delta_dist').iloc[0]
    
    premium_per_contract = best_call['mid_price'] * 100
    total_premium = premium_per_contract * contracts_to_sell
    yield_on_notional = total_premium / (engine.underlying_price * shares)
    
    print("\n--- Layer 4 Execution Plan ---")
    print(f"Strategy: Covered Call Write (Yield Harvesting)")
    print(f"Underlying Price: ${engine.underlying_price:.2f}")
    print(f"Contract: {best_call['contractSymbol']} (Strike: ${best_call['strike']:.2f}, DTE: {best_call['dte']})")
    print(f"Greeks: Delta: {best_call['delta']:.3f} | IV: {best_call['iv']:.3f} | Theta: {best_call['theta']:.3f}")
    print(f"Action: SELL TO OPEN {contracts_to_sell} contracts @ ${best_call['mid_price']:.2f}")
    print(f"Expected Premium Collected: ${total_premium:.2f}")
    print(f"Annualized Yield (Assuming 12 rolls/yr): {yield_on_notional * 12 * 100:.2f}%")
    
    # Update portfolio with pending options orders
    port['positions']['options'] = {
        best_call['contractSymbol']: {
            "contracts": -contracts_to_sell, # negative for short
            "avg_price": best_call['mid_price'],
            "type": "covered_call",
            "underlying": target_ticker
        }
    }
    
    with open(portfolio_path, 'w') as f:
        json.dump(port, f, indent=2)
        
    print("\nPortfolio updated with Layer 4 Option positions.")

if __name__ == "__main__":
    run_layer4_options_overlay()
