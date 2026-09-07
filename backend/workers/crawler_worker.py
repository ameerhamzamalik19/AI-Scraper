import dramatiq
import asyncio
import logging
from redis_client import redis_client
from crawler.crawler import Crawler
from config import crawler_settings, settings
from database import close_db_pool
from utils.chat_status_tracker import ChatStatusTracker
from utils.progress_tracker import get_progress_tracker

logger = logging.getLogger(__name__)

@dramatiq.actor(
    queue_name=settings.SCRAPING_QUEUE_NAME,
    max_retries=3,
    time_limit=600000
)
def crawl_website(job_id: str):
    """
    Main crawler worker.
    Pops a job from Redis and crawls the website with status tracking.
    """
    print(f"🔍 Starting crawl for job: {job_id}")
    
    # Get job data from Redis
    job_data = redis_client.get_job(job_id)
    if not job_data:
        print(f"❌ Job {job_id} not found in Redis")
        return
    
    print(f"📋 Job data: {job_data}")
    
    # Extract chat_id from job data
    chat_id = job_data.get('chat_id')
    url = job_data.get('url')
    
    # Update status to processing (Redis)
    redis_client.update_job_status(job_id, "processing")
    
    # ✅ Get progress tracker
    tracker = None
    if chat_id:
        tracker = get_progress_tracker(chat_id)
        tracker.update_stage('crawling', 5, f"Starting crawl of {url}...")
    
    crawler = None
    
    try:
        # Run the crawler
        async def run_crawler():
            nonlocal crawler
            crawler = Crawler(
                url=job_data['url'],
                project_id=job_data['project_id'],
                user_id=job_data['user_id'],
                chat_id=chat_id,
                page_id=job_data.get('page_id'),
                max_pages=crawler_settings.MAX_PAGES_PER_CRAWL
            )
            
            try:
                return await crawler.run()
            finally:
                await close_db_pool()
        
        # Run the async crawler
        results = asyncio.run(run_crawler())

        crawled_pages = results.get('crawled_pages', [])
        pages_crawled = results.get('pages_crawled', 0)
        
        # ✅ Update progress: crawling complete with page discovery
        if chat_id and tracker:
            pages_discovered = results.get('pages_discovered', 0)
            pages_crawled = results.get('pages_crawled', 0)
            
            # Update crawling progress - show we've found pages
            tracker.update_stage(
                'crawling', 
                50,  # 50% through crawling stage (halfway through 5-30% range)
                f"Found {pages_discovered} pages, crawled {pages_crawled}"
            )
        
        # Update job status to completed (Redis)
        redis_client.update_job_status(
            job_id,
            "completed",
            pages_crawled=results.get('pages_crawled', 0),
            crawled_pages=results.get('crawled_pages', []),
            pages_discovered=results.get('pages_discovered', 0),
            pages_failed=results.get('pages_failed', 0)
        )
        
        print(f"✅ Crawl completed for job: {job_id}")
        print(f"📄 Crawled pages for job {job_id}: {results.get('crawled_pages', [])}")
        print(f"📊 Results: {results}")
        
        # ✅ If chat_id exists and we have crawled pages, move to processing stage
                # ✅ Set pending_documents counter so the embedder knows when ALL pages are done.
        # Crawler._crawl_page() already dispatched process_document for every page,
        # so we must NOT send it again here for the first page (Bug #1 fix).
        
        # if chat_id and tracker and results.get('crawled_pages'):
        #     crawled_pages = results.get('crawled_pages', [])
        #     n_pages = len(crawled_pages)
        #     if n_pages > 0:
        #         from database_sync import execute_update as _eu
        #         _eu(
        #             "UPDATE chats SET pending_documents = %s WHERE id = %s",
        #             (n_pages, chat_id)
        #         )
        #         logger.info(f"📊 Set pending_documents = {n_pages} for chat {chat_id}")
        #         tracker.update_stage(
        #             'processing',
        #             0,
        #             f"Processing {n_pages} page(s)..."
        #         )
        if chat_id and tracker and results.get('crawled_pages'):
            tracker.update_stage('processing', 0, f"Processing {len(results['crawled_pages'])} page(s)...")
        
        # Broadcast final crawl summary via WebSocket
        if chat_id and crawler:
            try:
                from redis_pubsub import WebSocketPubSub
                
                # Send final summary
                summary = {
                    'type': 'crawl_summary',
                    'total_pages_crawled': results.get('pages_crawled', 0),
                    'total_pages_discovered': results.get('pages_discovered', 0),
                    'total_pages_failed': results.get('pages_failed', 0),
                    'urls': crawler.crawled_url_records if hasattr(crawler, 'crawled_url_records') else []
                }
                WebSocketPubSub.publish(chat_id, summary)
                logger.info(f"📡 Broadcasted crawl summary for chat {chat_id}")
            except Exception as e:
                logger.error(f"Failed to broadcast crawl summary: {e}")
        
    except Exception as e:
        error_msg = f"Crawl failed: {str(e)}"
        print(f"❌ {error_msg}")
        import traceback
        traceback.print_exc()
        
        # Update Redis job status
        redis_client.update_job_status(
            job_id,
            "failed",
            error=str(e)
        )
        
        # ✅ Update chat status using progress tracker
        if chat_id and tracker:
            tracker.mark_failed(error_msg)
        elif chat_id:
            # Fallback if tracker wasn't created
            ChatStatusTracker.mark_failed(chat_id, error_msg)
            
            # Broadcast failure via WebSocket
            try:
                from redis_pubsub import WebSocketPubSub
                WebSocketPubSub.publish(chat_id, {
                    'type': 'crawl_error',
                    'error': error_msg
                })
            except Exception:
                pass
        
        raise