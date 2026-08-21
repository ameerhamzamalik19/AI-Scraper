import dramatiq
import asyncio
from redis_client import redis_client
from crawler.crawler import Crawler
from config import crawler_settings, settings

@dramatiq.actor(
        queue_name=settings.SCRAPING_QUEUE_NAME, 
        max_retries=3, 
        time_limit=600000
    )
def crawl_website(job_id: str):
    """
    Main crawler worker.
    Pops a job from Redis and crawls the website.
    """
    print(f"🔍 Starting crawl for job: {job_id}")
    
    # Get job data from Redis
    job_data = redis_client.get_job(job_id)
    if not job_data:
        print(f"❌ Job {job_id} not found in Redis")
        return
    
    print(f"📋 Job data: {job_data}")
    
    # Update status to processing
    redis_client.update_job_status(job_id, "processing")
    
    try:
        # Run the crawler
        async def run_crawler():
            crawler = Crawler(
                url=job_data['url'],
                project_id=job_data['project_id'],
                user_id=job_data['user_id'],
                chat_id=job_data.get('chat_id'),
                page_id=job_data.get('page_id'),
                max_pages=crawler_settings.MAX_PAGES_PER_CRAWL
            )
            
            results = await crawler.run()
            return results
        
        # Run the async crawler
        results = asyncio.run(run_crawler())
        
        # Update job status to completed
        redis_client.update_job_status(
            job_id,
            "completed",
            pages_crawled=results['pages_crawled'],
            pages_discovered=results['pages_discovered'],
            pages_failed=results['pages_failed']
        )
        
        print(f"✅ Crawl completed for job: {job_id}")
        print(f"📊 Results: {results}")
        
    except Exception as e:
        print(f"❌ Crawl failed for job {job_id}: {e}")
        import traceback
        traceback.print_exc()
        
        redis_client.update_job_status(
            job_id,
            "failed",
            error=str(e)
        )