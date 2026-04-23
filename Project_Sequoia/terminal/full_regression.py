import urllib.request
import json
import time

BASE_URL = "http://localhost:9098"

def test_endpoint(name, url, check_func):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            if check_func(data):
                print(f"[PASS] {name}")
                return True
            else:
                print(f"[FAIL] {name} - Validation failed: {data}")
                return False
    except Exception as e:
        print(f"[FAIL] {name} - Request failed: {e}")
        return False

def run_all_tests():
    print("Running Full Regression Suite...")
    
    results = {}
    
    # Dashboard API - Quotes
    results['quotes'] = test_endpoint("Quotes API", f"{BASE_URL}/api/quotes?tickers=AAPL,MSFT",
                  lambda d: "AAPL" in d and "price" in d["AAPL"])
    
    # Dashboard API - 1D Chart
    results['chart_1d'] = test_endpoint("Chart API (1D)", f"{BASE_URL}/api/chart?ticker=AAPL&tf=1D",
                  lambda d: "labels" in d and "prices" in d)
    
    # 1D Chart - Check for full time axis (09:30-16:00 = 391 points)
    try:
        req = urllib.request.Request(f"{BASE_URL}/api/chart?ticker=AAPL&tf=1D", headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            labels = data.get('labels', [])
            if len(labels) == 391 and labels[0] == '09:30' and labels[-1] == '16:00':
                print("[PASS] Chart 1D Time Axis (391 labels, 09:30-16:00)")
                results['chart_1d_axis'] = True
            else:
                print(f"[FAIL] Chart 1D Time Axis - Expected 391 labels from 09:30-16:00, got {len(labels)}, {labels[0]}-{labels[-1] if labels else 'N/A'}")
                results['chart_1d_axis'] = False
    except Exception as e:
        print(f"[FAIL] Chart 1D Time Axis - {e}")
        results['chart_1d_axis'] = False
    
    # Option Monitor API - Expirations
    results['expirations'] = test_endpoint("Expirations API", f"{BASE_URL}/api/expirations?ticker=SPY",
                  lambda d: "expirations" in d and isinstance(d["expirations"], list) and len(d["expirations"]) > 0)
    
    # Screener API - Options chain
    results['screener'] = test_endpoint("Screener API", f"{BASE_URL}/api/screen?ticker=SPY",
                  lambda d: "results" in d and isinstance(d["results"], list) and len(d["results"]) > 0)
    
    # Ratio API - Check that RSI/MACD are non-zero
    results['ratio'] = test_endpoint("Ratio API", f"{BASE_URL}/api/ratio?t1=XLE&t2=SPY&tf=1Y&sma=200",
                  lambda d: "ratio" in d and "sma" in d and "rsi" in d)
    
    # Check RSI/MACD are not all zeros
    try:
        req = urllib.request.Request(f"{BASE_URL}/api/ratio?t1=XLE&t2=SPY&tf=1M&sma=50", headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            rsi = data.get('rsi', [])
            macd = data.get('macd', [])
            
            # Check RSI has non-zero values
            rsi_nonzero = [x for x in rsi if x != 0]
            macd_nonzero = [x for x in macd if x != 0]
            
            if len(rsi_nonzero) > 0 and len(macd_nonzero) > 0:
                print(f"[PASS] Ratio Indicators (RSI: {len(rsi_nonzero)} non-zero, MACD: {len(macd_nonzero)} non-zero)")
                results['ratio_indicators'] = True
            else:
                print(f"[FAIL] Ratio Indicators - RSI has {len(rsi_nonzero)} non-zero, MACD has {len(macd_nonzero)} non-zero")
                results['ratio_indicators'] = False
    except Exception as e:
        print(f"[FAIL] Ratio Indicators - {e}")
        results['ratio_indicators'] = False
    
    # Financials API - Watchlist
    results['financials'] = test_endpoint("Financials Watchlist", f"{BASE_URL}/api/sec/financials?action=watchlist",
                  lambda d: isinstance(d, list) and len(d) > 0)
    
    # Summary
    print("\n" + "="*50)
    print("SUMMARY:")
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    print(f"Passed: {passed}/{total}")
    for name, passed in results.items():
        status = "✓" if passed else "✗"
        print(f"  {status} {name}")
    print("="*50)
    
    return passed == total

if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
