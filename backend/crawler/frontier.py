from urllib.parse import urlparse, urljoin
from typing import Set, List, Tuple, Optional
from dataclasses import dataclass
import hashlib


@dataclass
class URLItem:
    """A URL in the frontier"""
    url: str
    depth: int


class URLFrontier:
    """
    URL Frontier for BFS crawling with limits.
    Only crawls same domain, no external links.
    """
    
    def __init__(self, start_url: str, max_pages: int = 100):
        self.base_domain = self._get_domain(start_url)
        self.max_pages = max_pages
        
        self.visited: Set[str] = set()          # URLs already crawled
        self.queued: Set[str] = set()           # URLs already in queue
        self.queue: List[URLItem] = []          # Queue of (url, depth)
        
        # Add start URL
        self._add_url(start_url, depth=0)
    
    def _get_domain(self, url: str) -> str:
        """Extract domain from URL"""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"
    
    def _normalize_url(self, url: str) -> str:
        """Normalize URL for deduplication (remove fragments, trailing slashes)"""
        parsed = urlparse(url)
        # Remove fragment
        normalized = parsed._replace(fragment='')
        # Remove trailing slash from path
        path = normalized.path
        if path.endswith('/') and len(path) > 1:
            path = path[:-1]
        normalized = normalized._replace(path=path)
        return normalized.geturl()
    
    def _is_same_domain(self, url: str) -> bool:
        """Check if URL belongs to the same domain"""
        domain = self._get_domain(url)
        return domain == self.base_domain
    
    def _should_add(self, url: str) -> bool:
        """Check if URL should be added to frontier"""
        normalized = self._normalize_url(url)
        
        # Skip if already visited or queued
        if normalized in self.visited or normalized in self.queued:
            return False
        
        # Skip external domains
        if not self._is_same_domain(url):
            return False
        
        # Skip common non-content extensions
        skip_extensions = [
            '.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp',  # Images
            '.mp4', '.mp3', '.avi', '.mov',                   # Media
            '.pdf', '.doc', '.docx', '.xls', '.xlsx',          # Documents
            '.zip', '.tar', '.gz', '.rar',                     # Archives
            '.xml', '.rss',                                    # Feeds
            '.css', '.js',                                     # Static assets
            '#', '?',                                          # Fragments/query params
        ]
        url_lower = url.lower()
        for ext in skip_extensions:
            if url_lower.endswith(ext):
                return False
        
        return True
    
    def _add_url(self, url: str, depth: int = 0):
        """Add URL to queue if valid"""
        if not self._should_add(url):
            return
        
        normalized = self._normalize_url(url)
        self.queued.add(normalized)
        self.queue.append(URLItem(url=url, depth=depth))
    
    def add_urls(self, urls: List[str], depth: int):
        """Add multiple URLs to frontier"""
        for url in urls:
            self._add_url(url, depth)
    
    def next_url(self) -> Optional[URLItem]:
        """Get next URL to crawl"""
        while self.queue:
            item = self.queue.pop(0)  # BFS
            
            # Skip if already visited
            normalized = self._normalize_url(item.url)
            if normalized in self.visited:
                continue
            
            return item
        
        return None
    
    def mark_visited(self, url: str):
        """Mark URL as visited"""
        normalized = self._normalize_url(url)
        self.visited.add(normalized)
    
    def has_next(self) -> bool:
        """Check if there are more URLs to crawl"""
        return len(self.queue) > 0 and len(self.visited) < self.max_pages
    
    def get_progress(self) -> dict:
        """Get crawl progress"""
        return {
            "visited": len(self.visited),
            "queued": len(self.queue),
            "max_pages": self.max_pages,
            "remaining": min(len(self.queue), self.max_pages - len(self.visited))
        }