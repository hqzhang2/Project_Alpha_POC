import yfinance as yf
import time

def test_fast_poll():
    ticker = yf.Ticker("AAPL")
    print("Starting fast poll test...")
    for i in range(5):
        # Using fast_info which is usually cached/fast
        price = ticker.fast_info['last_price']
        print(f"[{i}] AAPL Price: {price} @ {time.ctime()}")
        time.sleep(1)

if __name__ == "__main__":
    test_fast_poll()
