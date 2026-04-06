import unittest
from unittest.mock import patch, MagicMock
import json
import os
import sys

# Add the QA_terminal directory to the path so we can import news
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from news import get_top_news, get_cn_news

class TestNewsModule(unittest.TestCase):

    @patch('requests.get')
    def test_get_top_news_headline(self, mock_get):
        # Mock Finnhub response for headline
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{'headline': 'Test Headline', 'category': 'general'}]
        mock_get.return_value = mock_response

        result = get_top_news("headline")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['headline'], 'Test Headline')
        mock_get.assert_called()

    @patch('requests.get')
    def test_get_top_news_newsapi_tech(self, mock_get):
        # Mock NewsAPI response for technologies
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'articles': [
                {
                    'title': 'Tech News',
                    'description': 'AI is taking over',
                    'source': {'name': 'TechCrunch'},
                    'url': 'http://tech.com',
                    'publishedAt': '2026-04-05T10:00:00Z'
                }
            ]
        }
        mock_get.return_value = mock_response

        result = get_top_news("technologies")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['headline'], 'Tech News')
        self.assertEqual(result[0]['source'], 'TechCrunch')

    @patch('requests.get')
    def test_get_top_news_economics_filter(self, mock_get):
        # Mock NewsAPI business feed with mixed content
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'articles': [
                {'title': 'Fed Raises Rates', 'description': 'Inflation is high', 'source': {'name': 'R'}, 'url': 'u1', 'publishedAt': '2026-04-05T10:00:00Z'},
                {'title': 'Soccer Match Result', 'description': 'Game was tied', 'source': {'name': 'S'}, 'url': 'u2', 'publishedAt': '2026-04-05T10:00:00Z'}
            ]
        }
        mock_get.return_value = mock_response

        result = get_top_news("economics")
        # Should only keep the "Fed" article
        self.assertEqual(len(result), 1)
        self.assertIn('Fed', result[0]['headline'])

    @patch('requests.get')
    @patch('builtins.open', unittest.mock.mock_open(read_data='["AAPL", "TSLA"]'))
    @patch('os.path.exists', return_value=True)
    def test_get_cn_news(self, mock_exists, mock_get):
        # Mock Finnhub company news
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{'headline': 'Apple News', 'datetime': 1600000000}]
        mock_get.return_value = mock_response

        result = get_cn_news()
        # 2 tickers * 1 news item = 2 results
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['ticker'], 'AAPL')

if __name__ == '__main__':
    unittest.main()
