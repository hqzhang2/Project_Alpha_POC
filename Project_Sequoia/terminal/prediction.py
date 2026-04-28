import requests
import json
import logging

logger = logging.getLogger(__name__)

def get_predictions():
    """
    Fetches trending and active prediction markets from Polymarket.
    Filters for high-volume or interesting geopolitical/macro categories.
    """
    url = "https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=50"
    
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        raw_data = response.json()
        
        formatted_data = []
        for m in raw_data:
            q = m.get('question', '')
            prices_raw = m.get('outcomePrices')
            
            # Polymarket API often returns prices as a stringified JSON list
            prices = []
            if isinstance(prices_raw, str):
                try:
                    prices = json.loads(prices_raw)
                except: pass
            elif isinstance(prices_raw, list):
                prices = prices_raw
                
            if len(prices) >= 2:
                try:
                    yes_pct = float(prices[0]) * 100
                    
                    formatted_data.append({
                        'question': q,
                        'probability': round(yes_pct, 1),
                        'category': m.get('category', 'General'),
                        'volume': m.get('volume', 0),
                        'image': m.get('image', ''),
                        'url': f"https://polymarket.com/event/{m.get('slug', '')}"
                    })
                except: continue
        
        # Sort by volume or interestingness (current logic: volume descending)
        formatted_data.sort(key=lambda x: float(x.get('volume') or 0), reverse=True)
        
        return formatted_data
        
    except Exception as e:
        logger.error(f"Polymarket Fetch Error: {e}")
        return []
