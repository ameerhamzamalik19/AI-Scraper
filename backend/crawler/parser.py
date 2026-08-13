from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from typing import List, Set, Dict, Any
import re


class HTMLParser:
    """
    Parse HTML to extract:
    - Title, description, metadata
    - All internal links (same domain)
    - Clean text (for later processing)
    """
    
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.base_domain = self._get_domain(base_url)
    
    def _get_domain(self, url: str) -> str:
        """Extract domain from URL"""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"
    
    def _normalize_url(self, url: str) -> str:
        """Normalize URL (remove fragments, trailing slashes)"""
        parsed = urlparse(url)
        normalized = parsed._replace(fragment='')
        path = normalized.path
        if path.endswith('/') and len(path) > 1:
            path = path[:-1]
        normalized = normalized._replace(path=path)
        return normalized.geturl()
    
    def _is_internal_link(self, url: str) -> bool:
        """Check if link is internal (same domain)"""
        domain = self._get_domain(url)
        return domain == self.base_domain
    
    def extract_links(self, html: str, current_url: str) -> List[str]:
        """Extract all internal links from HTML"""
        soup = BeautifulSoup(html, 'html.parser')
        links = set()
        
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            
            # Skip empty links
            if not href or href.startswith('#'):
                continue
            
            # Skip javascript: and mailto: links
            if href.startswith('javascript:') or href.startswith('mailto:'):
                continue
            
            # Convert relative to absolute URL
            absolute_url = urljoin(current_url, href)
            
            # Only keep internal links
            if self._is_internal_link(absolute_url):
                normalized = self._normalize_url(absolute_url)
                links.add(normalized)
        
        # Limit links to prevent explosion
        return list(links)[:500]  # Max 500 links per page
    
    def extract_metadata(self, html: str, url: str) -> Dict[str, Any]:
        """Extract title and metadata from HTML"""
        soup = BeautifulSoup(html, 'html.parser')
        
        # Title
        title = soup.find('title')
        title_text = title.get_text().strip() if title else None
        
        # Meta description
        description = None
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc:
            description = meta_desc.get('content', '').strip()
        
        # Meta keywords
        keywords = None
        meta_keywords = soup.find('meta', attrs={'name': 'keywords'})
        if meta_keywords:
            keywords = meta_keywords.get('content', '').strip()
        
        # Canonical URL
        canonical = None
        link_canonical = soup.find('link', attrs={'rel': 'canonical'})
        if link_canonical:
            canonical = link_canonical.get('href', '').strip()
            if canonical:
                canonical = urljoin(url, canonical)
        
        return {
            'title': title_text,
            'description': description,
            'keywords': keywords,
            'canonical_url': canonical
        }
    
    def get_main_content(self, html: str) -> str:
        """
        Extract main content from HTML (basic cleaning).
        Full cleaning will be done by separate worker.
        """
        soup = BeautifulSoup(html, 'html.parser')
        
        # Remove script and style tags
        for tag in soup(['script', 'style', 'noscript', 'iframe', 'header', 'footer', 'nav']):
            tag.decompose()
        
        # Get text
        text = soup.get_text(separator='\n', strip=True)
        
        # Clean up whitespace
        text = re.sub(r'\n\s*\n', '\n\n', text)
        text = text.strip()
        
        return text