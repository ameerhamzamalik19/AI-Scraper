# utils/content_classifier.py
from enum import Enum
from typing import Dict, Any
from bs4 import BeautifulSoup
import re

class ContentType(Enum):
    ARTICLE = "article"
    DOCUMENTATION = "documentation"
    ECOMMERCE = "ecommerce"
    CARD_LISTING = "card_listing"
    LANDING_PAGE = "landing_page"
    DATA_TABLE = "data_table"
    MIXED = "mixed"

class ContentClassifier:
    def __init__(self):
        self.signals = {}
    
    def classify(self, soup: BeautifulSoup, url: str) -> ContentType:
        """Classify page type using heuristic signals"""
        signals = {
            'has_schema_product': self._has_schema_org_type(soup, 'Product'),
            'has_schema_article': self._has_schema_org_type(soup, ['Article', 'BlogPosting']),
            'has_schema_faq': self._has_schema_org_type(soup, 'FAQPage'),
            'has_product_price': bool(soup.select('[class*="price"], [itemprop="price"], .product-price')),
            'has_article_tag': bool(soup.find('article')),
            'has_code_blocks': bool(soup.select('pre code, .highlight, .code-block')),
            'has_table_of_contents': bool(soup.select('[class*="toc"], [id*="toc"], .table-of-contents')),
            'has_card_grid': bool(soup.select('[class*="grid"], [class*="cards"], .product-grid')),
            'has_breadcrumbs': bool(soup.select('[class*="breadcrumb"], .breadcrumbs')),
            'has_data_table': bool(soup.select('table.price-table, table.comparison-table, table.features')),
            'has_pagination': bool(soup.select('[class*="pagination"], .next, .prev')),
            'url_is_docs': self._check_url_pattern(url, ['/docs/', '/documentation/', '/api/', '/reference/']),
            'url_is_blog': self._check_url_pattern(url, ['/blog/', '/news/', '/post/', '/article/']),
            'url_is_product': self._check_url_pattern(url, ['/product/', '/item/', '/p/']),
            'content_density': self._calc_text_to_html_ratio(soup),
            'link_density': self._calc_link_density(soup),
        }
        return self._score_signals(signals)
    
    def _has_schema_org_type(self, soup: BeautifulSoup, types: list) -> bool:
        """Check if page has Schema.org JSON-LD of given type"""
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                import json
                data = json.loads(script.string)
                if isinstance(data, dict):
                    if data.get('@type') in types:
                        return True
                elif isinstance(data, list):
                    for item in data:
                        if item.get('@type') in types:
                            return True
            except:
                pass
        return False
    
    def _calc_text_to_html_ratio(self, soup: BeautifulSoup) -> float:
        """Calculate text-to-HTML ratio as proxy for content density"""
        text = len(soup.get_text(strip=True))
        html = len(str(soup))
        return text / max(html, 1)
    
    def _calc_link_density(self, soup: BeautifulSoup) -> float:
        """Calculate link-to-text ratio - high = navigation/boilerplate"""
        links = len(soup.find_all('a'))
        text = max(len(soup.get_text(strip=True)), 1)
        return links / text
    
    def _check_url_pattern(self, url: str, patterns: list) -> bool:
        return any(pattern in url for pattern in patterns)
    
    def _score_signals(self, signals: Dict[str, Any]) -> ContentType:
        # Weighted scoring logic
        scores = {
            ContentType.ARTICLE: 0,
            ContentType.DOCUMENTATION: 0,
            ContentType.ECOMMERCE: 0,
            ContentType.CARD_LISTING: 0,
            ContentType.DATA_TABLE: 0,
        }
        
        # Article signals
        if signals['has_schema_article']: scores[ContentType.ARTICLE] += 3
        if signals['has_article_tag']: scores[ContentType.ARTICLE] += 2
        if signals['url_is_blog']: scores[ContentType.ARTICLE] += 2
        if signals['content_density'] > 0.3: scores[ContentType.ARTICLE] += 1
        
        # Documentation signals
        if signals['has_code_blocks']: scores[ContentType.DOCUMENTATION] += 3
        if signals['has_table_of_contents']: scores[ContentType.DOCUMENTATION] += 2
        if signals['url_is_docs']: scores[ContentType.DOCUMENTATION] += 3
        
        # Ecommerce signals
        if signals['has_schema_product']: scores[ContentType.ECOMMERCE] += 3
        if signals['has_product_price']: scores[ContentType.ECOMMERCE] += 3
        if signals['url_is_product']: scores[ContentType.ECOMMERCE] += 2
        if signals['has_card_grid']: scores[ContentType.ECOMMERCE] += 1
        
        # Card listing signals
        if signals['has_card_grid'] and signals['has_pagination']:
            scores[ContentType.CARD_LISTING] += 3
        if signals['has_card_grid'] and not signals['has_product_price']:
            scores[ContentType.CARD_LISTING] += 1
        
        # Data table signals
        if signals['has_data_table']: scores[ContentType.DATA_TABLE] += 3
        
        return max(scores, key=scores.get)