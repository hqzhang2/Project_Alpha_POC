import json
import requests
import datetime
import os
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FINNHUB_API_KEY = os.environ.get('FINNHUB_API_KEY', 'd767d8hr01qm4b7t7tfgd767d8hr01qm4b7t7tg0')
NEWSAPI_KEY = os.environ.get('NEWSAPI_KEY', '038953ee9b8a4af986b2f758dd26b14b')

def _fetch_from_api(url, timeout=5):
    """Internal helper to handle API requests with error handling."""
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"API Fetch Error for {url.split('?')[0]}: {e}")
        return None

def _filter_by_keywords(data, keywords):
    """Filters news items based on headline and summary keywords."""
    if not data: return []
    return [
        x for x in data 
        if any(k in (str(x.get('headline','')) + ' ' + str(x.get('summary',''))).lower() for k in keywords)
    ]

def get_top_news(category="headline"):
    """
    Unified entry point for category-based news.
    Routes to Finnhub for general/M&A and NewsAPI for specialized sectors.
    """
    # 1. Finnhub Routes
    if category == "headline":
        res = _fetch_from_api(f"https://finnhub.io/api/v1/news?category=general&token={FINNHUB_API_KEY}")
        return res if res else []
            
    if category == "ma":
        res = _fetch_from_api(f"https://finnhub.io/api/v1/news?category=merger&token={FINNHUB_API_KEY}")
        return res if res else []
            
    # 2. NewsAPI Routes
    if category in ["economics", "markets", "technologies"]:
        api_cat = "technology" if category == "technologies" else "business"
        url = f"https://newsapi.org/v2/top-headlines?country=us&category={api_cat}&pageSize=100&apiKey={NEWSAPI_KEY}"
        
        raw_res = _fetch_from_api(url)
        if not raw_res: return []
        
        articles = raw_res.get('articles', [])
        formatted = []
        for item in articles:
            if item.get('title') == '[Removed]': continue
            
            # Map NewsAPI schema to terminal UI schema
            dt = item.get('publishedAt', '')
            ts = 0
            try:
                ts = int(datetime.datetime.strptime(dt, "%Y-%m-%dT%H:%M:%SZ").timestamp())
            except: pass
                
            formatted.append({
                'headline': item.get('title', ''),
                'summary': item.get('description', '') or '',
                'source': item.get('source', {}).get('name', ''),
                'url': item.get('url', ''),
                'datetime': ts
            })
            
        if category == "economics":
            kws = ['fed ', 'federal reserve', 'inflation', 'cpi', 'gdp', 'rates', 'powell', 'economy', 'jobless', 'payrolls', 'macro', 'central bank', 'interest rate', 'boj', 'ecb', 'pboc', 'opec', 'imf', 'world bank', 'deficit', 'debt', 'trade war', 'tariff', 'recession', 'monetary', 'fiscal', 'eurozone', 'brexit', 'emerging markets', 'emea', 'latam', 'brazil', 'mexico', 'saudi', 'nigeria', 'south africa', 'turkey']
            return _filter_by_keywords(formatted, kws)
        
        if category == "markets":
            kws = ['futures', 'dow', 'nasdaq', 's&p 500', 'bull market', 'bear market', 'rally', 'equities', 'dividend', 'yield', 'bond', 'treasury', 'etf', 'vix', 'stock', 'market', 'nikkei', 'dax', 'ftse', 'hang seng', 'shanghai composite', 'asx', 'nifty', 'stoxx', 'bovespa', 'ipc', 'global', 'international', 'emerging markets', 'currency', 'forex', 'commodity', 'gold', 'oil', 'crude', 'yen', 'euro', 'pound', 'yuan', 'real', 'peso', 'asia', 'europe', 'emea', 'latam', 'middle east', 'africa']
            return _filter_by_keywords(formatted, kws)
            
        return formatted
        
    return []

def get_cn_news():
    """Fetches news for tickers defined in watchlist.json."""
    watchlist_path = os.path.join(os.path.dirname(__file__), 'watchlist.json')
    tickers = ['AAPL', 'MSFT', 'SPY'] # Defaults
    
    if os.path.exists(watchlist_path):
        try:
            with open(watchlist_path, 'r') as f:
                tickers = json.load(f)
        except Exception as e:
            logger.error(f"Watchlist Read Error: {e}")
        
    today = datetime.datetime.now()
    d_to = today.strftime('%Y-%m-%d')
    d_from = (today - datetime.timedelta(days=3)).strftime('%Y-%m-%d')
    
    all_news = []
    for ticker in tickers:
        url = f"https://finnhub.io/api/v1/company-news?symbol={ticker}&from={d_from}&to={d_to}&token={FINNHUB_API_KEY}"
        res = _fetch_from_api(url)
        if res:
            for item in res:
                item['ticker'] = ticker
            all_news.extend(res)
            
    all_news.sort(key=lambda x: x.get('datetime', 0), reverse=True)
    return all_news
