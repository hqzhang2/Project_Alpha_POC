"""
Integration test - simulates server import behavior
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Clear cache and load fresh (like server does)
for mod in list(sys.modules.keys()):
    if 'financials' in mod.lower():
        del sys.modules[mod]

import importlib.util
spec = importlib.util.spec_from_file_location("financials", "financials.py")
financials = importlib.util.module_from_spec(spec)
spec.loader.exec_module(financials)

# Test AAPL
result = financials.get_financials('AAPL', 4, 'Q')

print("AAPL Test:")
print(f"  Income periods: {len(result['income'])}")
print(f"  Balance periods: {len(result['balance'])}")
print(f"  Info name: {result['info']['name']}")
print(f"  Info pe_ratio: {result['info'].get('pe_ratio')}")
print(f"  Metrics score: {result['metrics'].get('score')}")
print(f"  Metrics rating: {result['metrics'].get('rating')}")
print(f"  Metrics pe_ratio: {result['metrics'].get('pe_ratio')}")
print(f"  Metrics earnings_yield: {result['metrics'].get('earnings_yield')}")

# Test MSFT
result2 = financials.get_financials('MSFT', 4, 'Q')
print("\nMSFT Test:")
print(f"  Info pe_ratio: {result2['info'].get('pe_ratio')}")
print(f"  Metrics pe_ratio: {result2['metrics'].get('pe_ratio')}")
print(f"  Rating: {result2['metrics'].get('rating')}")

print("\n✅ Integration tests complete")
