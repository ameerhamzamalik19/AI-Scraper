import asyncio
import time
import logging
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse
from crawler.frontier import URLFrontier
from crawler.fetcher import Fetcher
from crawler.parser import HTMLParser
from crawler.storage import CrawlerStorage
from config import crawler_settings
from database_sync import execute_query
from utils.queue_dispatch import enqueue_worker

logger = logging.getLogger(__name__)


class Crawler:
    """
    Main crawler orchestrator.
    Crawls a website with BFS, max_pages limit, same domain only.
    Tracks all crawled URLs for display to the user.
    """
    
    def __init__(
        self,
        url: str,
        project_id: str,
        user_id: str,
        chat_id: str,
        page_id: str,
        max_pages: int = None
    ):
        self.url = url
        self.project_id = project_id
        self.user_id = user_id
        self.chat_id = chat_id
        self.page_id = page_id
        self.max_pages = max_pages or crawler_settings.MAX_PAGES_PER_CRAWL
        
        # ✅ Use page_id if provided, otherwise create new
        self.initial_page_id = page_id
        
        self.frontier = URLFrontier(url, self.max_pages)
        self.fetcher = Fetcher()
        self.crawled_url_records: List[Dict[str, Any]] = []
        self.results = {
            "pages_crawled": 0,
            "crawled_pages": [],
            "pages_discovered": 0,
            "pages_failed": 0,
            "errors": []
        }
    
    def _save_crawled_url_record(self, url: str, status: str, 
                              page_id: Optional[str] = None,
                              page_version_id: Optional[str] = None,
                              document_id: Optional[str] = None,
                              page_title: Optional[str] = None,
                              error: Optional[str] = None):
        """Store a crawled URL record in memory and database."""
        # Check if URL already exists in memory
        existing = next((r for r in self.crawled_url_records if r['url'] == url), None)
        if existing:
            # Update existing record
            existing['status'] = status
            if page_title:
                existing['page_title'] = page_title
            if error:
                existing['error'] = error
            if page_id:
                existing['page_id'] = page_id
            if document_id:
                existing['document_id'] = document_id
            if page_version_id:
                existing['page_version_id'] = page_version_id
        else:
            # Create new record
            record = {
                'url': url,
                'page_title': page_title or '',
                'document_id': document_id,
                'page_id': page_id,
                'page_version_id': page_version_id,
                'status': status,
                'error': error,
                'crawled_at': time.time()
            }
            self.crawled_url_records.append(record)
        
        # Store in database - UPDATE instead of INSERT
        try:
            execute_query(
                """
                INSERT INTO crawled_urls 
                (chat_id, url, page_title, document_id, page_id, page_version_id, status, error_message)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (chat_id, url) DO UPDATE SET
                    page_title = EXCLUDED.page_title,
                    document_id = EXCLUDED.document_id,
                    page_id = EXCLUDED.page_id,
                    page_version_id = EXCLUDED.page_version_id,
                    status = EXCLUDED.status,
                    error_message = EXCLUDED.error_message,
                    crawled_at = NOW()
                """,
                (self.chat_id, url, page_title, document_id, page_id, page_version_id, status, error)
            )
            logger.debug(f"💾 Saved crawled URL: {url} ({status})")
        except Exception as e:
            logger.error(f"Failed to save crawled URL record: {e}")
            raise
        # Broadcast status update via WebSocket
        self._broadcast_crawl_progress()
    
    def _broadcast_crawl_progress(self):
        """Broadcast crawl progress via WebSocket using Redis PubSub."""
        try:
            from redis_pubsub import WebSocketPubSub
            
            total = len(self.crawled_url_records)
            completed = len([r for r in self.crawled_url_records if r.get('status') == 'completed'])
            failed = len([r for r in self.crawled_url_records if r.get('status') == 'failed'])
            pending = len([r for r in self.crawled_url_records if r.get('status') == 'pending'])
            processing = len([r for r in self.crawled_url_records if r.get('status') == 'processing'])
            
            status_message = {
                'type': 'crawl_progress',
                'total': total,
                'completed': completed,
                'failed': failed,
                'pending': pending,
                'processing': processing,
                'urls': [
                    {
                        'url': r['url'],
                        'title': r.get('page_title', '') or r['url'],
                        'status': r.get('status', 'pending'),
                        'error': r.get('error')
                    }
                    for r in self.crawled_url_records
                ]
            }
            
            # Use WebSocketPubSub.publish
            WebSocketPubSub.publish(self.chat_id, status_message)
            logger.debug(f"📡 Broadcasted crawl progress: {completed}/{total}")
            
        except ImportError as e:
            logger.warning(f"Could not import WebSocketPubSub: {e}")
        except Exception as e:
            logger.error(f"Failed to broadcast crawl progress: {e}")
    
    async def _crawl_page(self, url: str, depth: int = 0) -> Optional[Dict[str, Any]]:
        """
        Crawl a single page and store results.
        ✅ Now tracks depth for crawl depth enforcement.
        """
        try:
            # Mark as processing
            self._save_crawled_url_record(url, 'processing')
            
            # Fetch content
            fetch_result = await self.fetcher.fetch(url)
            
            if not fetch_result.get('success'):
                self.results["pages_failed"] += 1
                error_msg = fetch_result.get('error', 'Unknown error')
                self.results["errors"].append({
                    "url": url,
                    "error": error_msg
                })
                self._save_crawled_url_record(url, 'failed', error=error_msg)
                return None
            
            # Parse HTML
            parser = HTMLParser(url)
            html = fetch_result.get('html')
            metadata = parser.extract_metadata(html, url)
            links = parser.extract_links(html, url)
            
            # ✅ FIX: Create or get page first, then create page version
            # The page creation should happen BEFORE page version creation
            if url == self.url and self.initial_page_id:
                # If this is the seed URL and we have a page_id, use it
                # But we need to get the page from the database first
                # Let's check if we can find the page by ID
                try:
                    from database import get_db_connection
                    async with get_db_connection() as conn:
                        page_result = await conn.fetchrow(
                            "SELECT id, chat_id, project_id, url, normalized_url FROM pages WHERE id = $1",
                            self.initial_page_id
                        )
                        if page_result:
                            page = {
                                "id": page_result["id"],
                                "chat_id": page_result["chat_id"],
                                "project_id": page_result["project_id"],
                                "url": page_result["url"],
                                "normalized_url": page_result["normalized_url"]
                            }
                        else:
                            # Page doesn't exist, create new one
                            page = await CrawlerStorage.get_or_create_page(
                                project_id=self.project_id,
                                chat_id=self.chat_id,
                                url=url,
                                normalized_url=url
                            )
                except Exception as e:
                    logger.warning(f"Could not fetch existing page: {e}")
                    # Fallback: create new page
                    page = await CrawlerStorage.get_or_create_page(
                        project_id=self.project_id,
                        chat_id=self.chat_id,
                        url=url,
                        normalized_url=url
                    )
            else:
                # Always create a new page for non-seed URLs
                page = await CrawlerStorage.get_or_create_page(
                    project_id=self.project_id,
                    chat_id=self.chat_id,
                    url=url,
                    normalized_url=url
                )
            
            # ✅ Now create the page version with the correct page_id
            version = await CrawlerStorage.create_page_version(
                page_id=page['id'],  # ← page_id is required, not project_id
                url=url,
                html=html,
                metadata=metadata,
                status_code=fetch_result.get('status_code', 200),
                content_type=fetch_result.get('content_type', 'text/html'),
                response_size=fetch_result.get('response_size', 0),
                fetch_method=fetch_result.get('method', 'httpx')
            )

            # Trigger processor worker
            if self.chat_id:
                from database_sync import execute_update as _eu
                _eu(
                    "UPDATE chats SET pending_documents = pending_documents + 1 WHERE id = %s",
                    (self.chat_id,)
                )

            # Trigger processor worker
            enqueue_worker(
                "workers.processor_worker.process_document",
                self.chat_id,
                version['document_id'],
            )
            
            print(f"📤 Triggered processor for document: {version['document_id']}")
            
            # Mark as visited in frontier
            self.frontier.mark_visited(url)
            
            # ✅ Add discovered links to frontier with depth tracking
            current_depth = depth + 1
            new_links = [link for link in links if link != url]
            
            # Limit links per page
            if len(new_links) > crawler_settings.MAX_LINKS_PER_PAGE:
                new_links = new_links[:crawler_settings.MAX_LINKS_PER_PAGE]
            
            # ✅ Enforce crawl depth - only add links if within max depth
            if current_depth <= crawler_settings.MAX_CRAWL_DEPTH:
                added_count = self.frontier.add_urls(new_links, current_depth)
                self.results["pages_discovered"] += added_count
                print(f"   📊 Added {added_count} new links at depth {current_depth}")
            else:
                print(f"   ⏭️ Skipping {len(new_links)} links at depth {current_depth} (max {crawler_settings.MAX_CRAWL_DEPTH})")
            
            self.results["pages_crawled"] += 1
            
            page_data = {
                'url': url,
                'document_id': version['document_id'],
                'page_id': page['id'],
                'page_version_id': version['id'],
                'title': metadata.get('title', ''),
                'links_count': len(new_links),
                'depth': depth
            }
            self.results["crawled_pages"].append(page_data)
            
            # The page is not ready until processing, chunking, and embedding succeed.
            self._save_crawled_url_record(
                url=url,
                status='processing',
                page_id=page['id'],
                page_version_id=version['id'],
                document_id=version['document_id'],
                page_title=metadata.get('title', '')
            )
            
            print(f"   ✅ Crawled: {url} ({self.results['pages_crawled']}/{self.max_pages})")
            print(f"   📊 Found {len(new_links)} new links")
            
            return {
                "page": page,
                "version": version,
                "metadata": metadata,
                "links": new_links,
                "depth": depth
            }
            
        except Exception as e:
            print(f"❌ Error crawling {url}: {e}")
            self.results["pages_failed"] += 1
            error_msg = str(e)
            self.results["errors"].append({
                "url": url,
                "error": error_msg
            })
            self._save_crawled_url_record(url, 'failed', error=error_msg)
            return None
    
    async def run(self) -> Dict[str, Any]:
        """
        Run the crawler.
        ✅ Now enforces both MAX_PAGES_PER_CRAWL and MAX_CRAWL_DEPTH.
        """
        print(f"🕷️ Starting crawl for: {self.url}")
        print(f"📊 Max pages: {self.max_pages}")
        print(f"📊 Max depth: {crawler_settings.MAX_CRAWL_DEPTH}")
        print("-" * 50)
        
        # Mark initial URL as pending
        self._save_crawled_url_record(self.url, 'pending')
        
        crawl_count = 0
        
        while self.frontier.has_next() and crawl_count < self.max_pages:
            url_item = self.frontier.next_url()
            if not url_item:
                break
            
            # ✅ Add delay between requests
            if crawl_count > 0:
                await asyncio.sleep(crawler_settings.REQUEST_DELAY)
            
            # ✅ Crawl the page with depth tracking
            result = await self._crawl_page(url_item.url, url_item.depth)
            
            if result:
                crawl_count += 1
        
        # Clean up
        await self.fetcher.close()
        
        # Broadcast final progress
        self._broadcast_crawl_progress()
        
        print("-" * 50)
        print(f"✅ Crawl completed!")
        print(f"📊 Pages crawled: {self.results['pages_crawled']}")
        print(f"📄 Crawled pages:")
        for page in self.results["crawled_pages"]:
            depth_info = f" (depth {page.get('depth', 'N/A')})" if page.get('depth') is not None else ""
            print(f"   - {page.get('url', 'Unknown URL')}{depth_info} (doc: {page.get('document_id', 'N/A')})")
        print(f"📊 Pages discovered: {self.results['pages_discovered']}")
        print(f"📊 Pages failed: {self.results['pages_failed']}")
        
        return self.results