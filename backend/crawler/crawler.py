import asyncio
import time
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse
from workers.processor_worker import process_document
from crawler.frontier import URLFrontier
from crawler.fetcher import Fetcher
from crawler.parser import HTMLParser
from crawler.storage import CrawlerStorage
from config import crawler_settings


class Crawler:
    """
    Main crawler orchestrator.
    Crawls a website with BFS, max_pages limit, same domain only.
    """
    
    def __init__(
        self,
        url: str,
        project_id: str,
        user_id: str,
        chat_id: str,
        page_id: str,
        max_pages: int = 100
    ):
        self.url = url
        self.project_id = project_id
        self.user_id = user_id
        self.chat_id = chat_id
        self.page_id = page_id  # The original page that started this crawl
        self.max_pages = max_pages
        
        self.frontier = URLFrontier(url, max_pages)
        self.fetcher = Fetcher()
        self.results = {
            "pages_crawled": 0,
            "pages_discovered": 0,
            "pages_failed": 0,
            "errors": []
        }
    
    async def _crawl_page(self, url: str) -> Optional[Dict[str, Any]]:
        """Crawl a single page and store results"""
        try:
            # Fetch content
            fetch_result = await self.fetcher.fetch(url)
            
            if not fetch_result.get('success'):
                self.results["pages_failed"] += 1
                self.results["errors"].append({
                    "url": url,
                    "error": fetch_result.get('error', 'Unknown error')
                })
                return None
            
            # Parse HTML
            parser = HTMLParser(url)
            html = fetch_result.get('html')
            metadata = parser.extract_metadata(html, url)
            links = parser.extract_links(html, url)
            
            # Store in database
            # Always create a new page for this crawl
            page = await CrawlerStorage.get_or_create_page(
                project_id=self.project_id,
                chat_id=self.chat_id,
                url=url,
                normalized_url=url
            )
            
            version = await CrawlerStorage.create_page_version(
                page_id=page['id'],
                url=url,
                html=html,
                metadata=metadata,
                status_code=fetch_result.get('status_code', 200),
                content_type=fetch_result.get('content_type', 'text/html'),
                response_size=fetch_result.get('response_size', 0),
                fetch_method=fetch_result.get('method', 'httpx')
            )

            # ✅ TRIGGER PROCESSOR WORKER
            process_document.send(version['document_id'])  # ← Sends to processing_queue

            print(f"📤 Triggered processor for document: {version['document_id']}")
            
            # Mark as visited in frontier
            self.frontier.mark_visited(url)
            
            # Add discovered links to frontier
            current_depth = 0
            # Since we're using BFS without depth tracking for simplicity,
            # we'll just add all links with a default depth
            # The max_pages limit will stop the crawl
            
            # Filter out the current URL to avoid self-loops
            new_links = [link for link in links if link != url]
            
            # Limit new links to prevent explosion
            if len(new_links) > 100:
                new_links = new_links[:100]
            
            self.frontier.add_urls(new_links, current_depth + 1)
            self.results["pages_discovered"] += len(new_links)
            self.results["pages_crawled"] += 1
            
            print(f"   ✅ Crawled: {url} ({self.results['pages_crawled']}/{self.max_pages})")
            print(f"   📊 Found {len(new_links)} new links")
            
            return {
                "page": page,
                "version": version,
                "metadata": metadata,
                "links": new_links
            }
            
        except Exception as e:
            print(f"❌ Error crawling {url}: {e}")
            self.results["pages_failed"] += 1
            self.results["errors"].append({
                "url": url,
                "error": str(e)
            })
            return None
    
    async def run(self) -> Dict[str, Any]:
        """Run the crawler"""
        print(f"🕷️ Starting crawl for: {self.url}")
        print(f"📊 Max pages: {self.max_pages}")
        print("-" * 50)
        
        crawl_count = 0
        
        while self.frontier.has_next() and crawl_count < self.max_pages:
            url_item = self.frontier.next_url()
            if not url_item:
                break
            
            # Add delay between requests
            if crawl_count > 0:
                await asyncio.sleep(crawler_settings.REQUEST_DELAY)
            
            # Crawl the page
            result = await self._crawl_page(url_item.url)
            
            if result:
                crawl_count += 1
        
        # Clean up
        await self.fetcher.close()
        
        print("-" * 50)
        print(f"✅ Crawl completed!")
        print(f"📊 Pages crawled: {self.results['pages_crawled']}")
        print(f"📊 Pages discovered: {self.results['pages_discovered']}")
        print(f"📊 Pages failed: {self.results['pages_failed']}")
        
        return self.results