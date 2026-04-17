import json
import yfinance as yf

portfolio_path = '/Users/chuck/.openclaw/workspace/Project_Nine_Street/paper_portfolio.json'
with open(portfolio_path, 'r') as f:
    port = json.load(f)

# Get real DBC price
dbc = yf.Ticker("DBC")
hist = dbc.history(period="1d")
dbc_price = round(hist['Close'].iloc[-1], 2)

nav = port['account']['total_nav']
shares = int(nav / dbc_price)

# Update Equity
port['positions']['equities']['DBC'] = {
    "shares": shares,
    "entry_price": dbc_price,
    "current_price": dbc_price,
    "allocation_pct": 100.0,
    "strategy": "NS-Regime-1"
}

# Update Options
contracts = shares // 100
# Realistic premium for ~30 delta, 33% IV, 27 DTE
assumed_premium = 0.45 

port['positions']['options'] = {
    "DBC260515C00031000": {
        "contracts": -contracts,
        "entry_price": assumed_premium,
        "current_price": assumed_premium,
        "type": "covered_call",
        "underlying": "DBC",
        "strike": 31.00,
        "dte_at_entry": 27
    }
}

# Update cash (NAV - equity cost + option premium collected)
equity_cost = shares * dbc_price
premium_collected = contracts * 100 * assumed_premium
port['account']['cash'] = round(nav - equity_cost + premium_collected, 2)
port['account']['initial_balance'] = 500000.0

with open(portfolio_path, 'w') as f:
    json.dump(port, f, indent=2)

print(f"Updated: DBC Price=${dbc_price}, Shares={shares}, Option Premium=${assumed_premium}, Contracts={contracts}")
