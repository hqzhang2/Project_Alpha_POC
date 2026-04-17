import json
import yfinance as yf
from datetime import datetime

def update_portfolio_pnl():
    portfolio_path = '/Users/chuck/.openclaw/workspace/Project_Nine_Street/paper_portfolio.json'
    try:
        with open(portfolio_path, 'r') as f:
            port = json.load(f)
    except Exception as e:
        print(f"Error reading portfolio: {e}")
        return

    total_equity_value = 0
    total_option_liability = 0
    
    # Update Equities
    if 'equities' in port['positions']:
        for ticker, data in port['positions']['equities'].items():
            tkr = yf.Ticker(ticker)
            hist = tkr.history(period="1d")
            if not hist.empty:
                current_price = round(hist['Close'].iloc[-1], 2)
                data['current_price'] = current_price
                position_value = current_price * data['shares']
                total_equity_value += position_value
                # Calculate individual PnL
                data['pnl'] = round((current_price - data['entry_price']) * data['shares'], 2)
                data['pnl_pct'] = round((current_price / data['entry_price'] - 1) * 100, 2)
            else:
                total_equity_value += data['current_price'] * data['shares']

    # Update Options (Simplified pricing for paper portfolio)
    if 'options' in port['positions']:
        for contract, data in port['positions']['options'].items():
            underlying = data['underlying']
            strike = data.get('strike', 0)
            
            # Get underlying price
            utkr = yf.Ticker(underlying)
            uhist = utkr.history(period="1d")
            u_price = uhist['Close'].iloc[-1] if not uhist.empty else port['positions']['equities'].get(underlying, {}).get('current_price', 0)
            
            # Simple intrinsic value + remaining time value estimate
            intrinsic = max(0, u_price - strike) if data['type'] == 'covered_call' else max(0, strike - u_price)
            # Rough estimate: decay time value
            time_value = max(0, data['entry_price'] - intrinsic) * 0.9 # decay 10% daily as a placeholder
            
            current_opt_price = round(intrinsic + time_value, 2)
            data['current_price'] = current_opt_price
            
            # Liability (negative contracts)
            position_value = current_opt_price * data['contracts'] * 100 # contracts is negative
            total_option_liability += position_value
            
            # PnL for short options: entry - current
            data['pnl'] = round((data['entry_price'] - current_opt_price) * abs(data['contracts']) * 100, 2)
            data['pnl_pct'] = round((1 - current_opt_price / data['entry_price']) * 100 if data['entry_price'] > 0 else 0, 2)

    # Update Account
    cash = port['account']['cash']
    new_nav = round(cash + total_equity_value + total_option_liability, 2)
    
    # Save to history if NAV changed or just daily
    today_str = datetime.now().strftime("%Y-%m-%d")
    if port['account']['last_updated'] != today_str:
        if 'history' not in port:
            port['history'] = []
        port['history'].append({
            "date": port['account']['last_updated'],
            "nav": port['account']['total_nav']
        })
    
    port['account']['total_nav'] = new_nav
    port['account']['last_updated'] = today_str
    
    with open(portfolio_path, 'w') as f:
        json.dump(port, f, indent=2)
        
    print(f"Portfolio updated successfully for {today_str}. New NAV: ${new_nav}")

if __name__ == "__main__":
    update_portfolio_pnl()
