import re

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/NS_QA/server.py', 'r') as f:
    content = f.read()

# Add the /api/live_feed endpoint
feed_code = """
        if path == '/api/live_feed':
            try:
                # Read portfolio to know what to fetch
                with open(os.path.join(os.path.dirname(__file__), '..', 'paper_portfolio.json'), 'r') as f:
                    port = json.load(f)
                
                tickers = list(port['positions'].get('equities', {}).keys())
                if not tickers:
                    tickers = ['SPY']
                
                # Fetch live data (1 minute interval for the last day)
                feed_data = []
                for ticker in tickers:
                    tkr = yf.Ticker(ticker)
                    live = tkr.history(period="1d", interval="1m")
                    if not live.empty:
                        last_price = float(live['Close'].iloc[-1])
                        prev_close = float(tkr.history(period="5d")['Close'].iloc[-2]) # approx prev close
                        pct_change = ((last_price / prev_close) - 1) * 100
                        feed_data.append({
                            'ticker': ticker,
                            'price': round(last_price, 2),
                            'change': round(pct_change, 2),
                            'timestamp': live.index[-1].strftime('%H:%M:%S EST')
                        })
                
                # We can also add simulated or recent system events
                events = []
                if 'history' in port and len(port['history']) > 0:
                    events.append({
                        'time': port['account']['last_updated'],
                        'message': f"Daily PnL Updated. NAV: ${port['account']['total_nav']}"
                    })
                events.append({
                    'time': feed_data[0]['timestamp'] if feed_data else 'Now',
                    'message': "Live Market Data Fetched"
                })

                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'prices': feed_data, 'events': events}).encode())
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
            return
"""

# Insert before 'super().do_GET()'
content = content.replace("super().do_GET()", feed_code + "\n        super().do_GET()")

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/NS_QA/server.py', 'w') as f:
    f.write(content)
