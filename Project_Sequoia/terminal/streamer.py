import asyncio
import base64
import json
import websockets
import time

# Yahoo Finance uses Protobuf over WebSocket. 
# For this POC, we'll use a polling fallback that *looks* live (1-3 second intervals)
# or implement a real WS client if we add a protobuf parser.

class LiveStreamer:
    def __init__(self, tickers):
        self.tickers = tickers
        self.data = {}
        self.running = False

    async def poll_yfinance(self):
        """Simulated 'live' stream via high-frequency polling."""
        import yfinance as yf
        while self.running:
            try:
                # Grouped download for efficiency
                tickers_str = " ".join(self.tickers)
                batch = yf.download(tickers_str, period="1d", interval="1m", progress=False, group_by="ticker")
                
                for ticker in self.tickers:
                    if ticker in batch:
                        df = batch[ticker]
                        if not df.empty:
                            last_row = df.iloc[-1]
                            self.data[ticker] = {
                                "price": float(last_row['Close']),
                                "timestamp": time.time()
                            }
                await asyncio.sleep(2) # 2-second 'live' feel
            except Exception as e:
                print(f"Stream error: {e}")
                await asyncio.sleep(5)

    def start(self):
        self.running = True
        asyncio.create_task(self.poll_yfinance())

