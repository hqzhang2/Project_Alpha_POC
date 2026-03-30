import requests
import sys

def run_regression():
    base_url = "http://localhost:9098/api/sec/financials"
    tickers = ["AAPL", "MSFT", "GOOGL", "AMZN"]
    
    print("--- 🔬 Starting Financial Analyzer Regression Tests ---")
    
    for ticker in tickers:
        print(f"\nTesting {ticker}...")
        try:
            resp = requests.get(f"{base_url}?ticker={ticker}&periods=4&type=Q")
            if resp.status_code != 200:
                print(f"❌ {ticker}: Failed with status {resp.status_code}")
                continue
                
            data = resp.json()
            
            # Check for core keys
            required_keys = ['income', 'balance', 'cashflow', 'metrics']
            missing = [k for k in required_keys if k not in data]
            if missing:
                print(f"❌ {ticker}: Missing root keys {missing}")
                continue

            # Check for data in tabs
            if not data['income'] or not data['balance'] or not data['cashflow']:
                print(f"❌ {ticker}: One or more data tabs are empty")
                continue

            # Check Graham metrics
            metrics = data['metrics']
            if 'graham_number' not in metrics or 'valuation_score' not in metrics:
                print(f"❌ {ticker}: Valuation metrics are missing")
                continue
                
            print(f"✅ {ticker}: All checks passed. Score: {metrics['valuation_score']}, Rating: {metrics['rating']}")
            
        except Exception as e:
            print(f"❌ {ticker}: Exception occurred - {str(e)}")

    print("\n--- Regression Finished ---")

if __name__ == "__main__":
    run_regression()
