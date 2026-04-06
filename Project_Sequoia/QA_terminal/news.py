import json
import requests
import datetime
import os

FINNHUB_API_KEY = 'd767d8hr01qm4b7t7tfgd767d8hr01qm4b7t7tg0'
NEWSAPI_KEY = '038953ee9b8a4af986b2f758dd26b14b'

def get_top_news(category="headline"):
    # Finnhub handles Main Headlines and M&A
    if category == "headline":
        url = f"https://finnhub.io/api/v1/news?category=general&token={FINNHUB_API_KEY}"
        try:
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            return []
            
    if category == "ma":
        url = f"https://finnhub.io/api/v1/news?category=merger&token={FINNHUB_API_KEY}"
        try:
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            return []
            
    # NewsAPI handles specific categories
    if category in ["economics", "markets", "technologies"]:
        newsapi_cat = "business"
        if category == "technologies":
            newsapi_cat = "technology"
            
        url = f"https://newsapi.org/v2/top-headlines?country=us&category={newsapi_cat}&pageSize=100&apiKey={NEWSAPI_KEY}"
        try:
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            raw_data = response.json().get('articles', [])
            
            formatted_data = []
            for item in raw_data:
                if item.get('title') == '[Removed]': continue
                
                dt = item.get('publishedAt', '')
                unix_time = 0
                if dt:
                    try:
                        unix_time = int(datetime.datetime.strptime(dt, "%Y-%m-%dT%H:%M:%SZ").timestamp())
                    except:
                        pass
                        
                formatted_data.append({
                    'headline': item.get('title', ''),
                    'summary': item.get('description', '') or '',
                    'source': item.get('source', {}).get('name', ''),
                    'url': item.get('url', ''),
                    'datetime': unix_time
                })
                
            if category == "economics":
                # Broaden economics to include global macro, trade, and debt keywords
                kws = ['fed ', 'federal reserve', 'inflation', 'cpi', 'gdp', 'rates', 'powell', 'economy', 'jobless', 'payrolls', 'macro', 'central bank', 'interest rate', 'boj', 'ecb', 'pboc', 'opec', 'imf', 'world bank', 'deficit', 'debt', 'trade war', 'tariff', 'recession', 'monetary', 'fiscal', 'eurozone', 'brexit', 'emerging markets', 'emea', 'latam', 'brazil', 'mexico', 'saudi', 'nigeria', 'south africa', 'turkey']
                formatted_data = [x for x in formatted_data if any(k in (str(x.get('headline','')) + ' ' + str(x.get('summary',''))).lower() for k in kws)]
            elif category == "markets":
                # Ensure Asia/Europe/EMEA/LATAM indices are captured
                kws = ['futures', 'dow', 'nasdaq', 's&p 500', 'bull market', 'bear market', 'rally', 'equities', 'dividend', 'yield', 'bond', 'treasury', 'etf', 'vix', 'stock', 'market', 'nikkei', 'dax', 'ftse', 'hang seng', 'shanghai composite', 'asx', 'nifty', 'stoxx', 'bovespa', 'ipc', 'global', 'international', 'emerging markets', 'currency', 'forex', 'commodity', 'gold', 'oil', 'crude', 'yen', 'euro', 'pound', 'yuan', 'real', 'peso', 'asia', 'europe', 'emea', 'latam', 'middle east', 'africa']
                formatted_data = [x for x in formatted_data if any(k in (str(x.get('headline','')) + ' ' + str(x.get('summary',''))).lower() for k in kws)]
                
            return formatted_data
        except Exception as e:
            print(f"Error fetching NewsAPI {category}: {e}")
            return []
    return []

def get_cn_news():
    watchlist_path = os.path.join(os.path.dirname(__file__), 'watchlist.json')
    tickers = []
    try:
        if os.path.exists(watchlist_path):
            with open(watchlist_path, 'r') as f:
                tickers = json.load(f)
    except Exception as e:
        pass
        
    if not tickers:
        tickers = ['AAPL', 'MSFT', 'SPY']
        
    today = datetime.datetime.now()
    yesterday = today - datetime.timedelta(days=3)
    d_to = today.strftime('%Y-%m-%d')
    d_from = yesterday.strftime('%Y-%m-%d')
    
    all_news = []
    for ticker in tickers:
        url = f"https://finnhub.io/api/v1/company-news?symbol={ticker}&from={d_from}&to={d_to}&token={FINNHUB_API_KEY}"
        try:
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            news_items = response.json()
            for item in news_items:
                item['ticker'] = ticker
            all_news.extend(news_items)
        except Exception as e:
            pass
            
    all_news.sort(key=lambda x: x.get('datetime', 0), reverse=True)
    return all_news
