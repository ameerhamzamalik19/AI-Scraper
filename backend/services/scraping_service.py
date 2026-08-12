from typing import List, Optional
from database import get_db_connection
from utils.helpers import generate_uuid, get_current_datetime, safe_datetime_for_db
from redis_client import redis_client
from exceptions import DatabaseError


class ScrapingService:
    """Service for scraping operations"""
    
    @staticmethod
    async def create_scraping_jobs(
        urls: List[str], 
        project_id: str, 
        user_id: str,
        conn
    ) -> Optional[str]:
        """Create scraping jobs for URLs"""
        now = get_current_datetime()
        safe_now = safe_datetime_for_db(now)
        
        scraping_job_id = None
        
        for url in urls:
            # Add scraping job to Redis
            scraping_job_id = redis_client.add_scraping_job(url, project_id, user_id)
            
            # Create a crawl job for this URL
            if scraping_job_id:
                crawl_job_id = generate_uuid()
                await conn.execute(
                    """INSERT INTO crawl_jobs 
                       (id, project_id, status, start_url, created_at, updated_at) 
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    crawl_job_id, project_id, 'DISCOVERED', url, safe_now, safe_now
                )
                
                # Add to crawl URL frontier
                await conn.execute(
                    """INSERT INTO crawl_urls 
                       (id, crawl_job_id, url, normalized_url, status, discovered_at, created_at, updated_at) 
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
                    generate_uuid(), crawl_job_id, url, url.lower().strip(), 'PENDING', 
                    safe_now, safe_now, safe_now
                )
            elif not redis_client.is_available():
                print("Redis unavailable - URL detected but not queued for scraping")
        
        return scraping_job_id
    
    @staticmethod
    async def get_scraping_jobs(chat_id: str, user_id: Optional[str] = None) -> List[dict]:
        """Get scraping jobs for a chat"""
        from services.user_service import UserService
        
        if not user_id:
            user_id = await UserService.get_or_create_user()
        
        try:
            async with get_db_connection() as conn:
                # Get crawl jobs for this page through project
                jobs = await conn.fetch(
                    """SELECT cj.id, cj.start_url as url, cj.status, cj.error_message, cj.created_at, cj.updated_at
                       FROM crawl_jobs cj
                       JOIN projects pr ON cj.project_id = pr.id
                       WHERE pr.user_id = $1 AND cj.project_id = (
                           SELECT project_id FROM pages WHERE id = $2
                       )
                       ORDER BY cj.created_at DESC""",
                    user_id, chat_id
                )
                
                result = []
                for job in jobs:
                    result.append({
                        "id": str(job['id']),
                        "url": job['url'],
                        "status": job['status'],
                        "error": job['error_message'],
                        "created_at": job['created_at'].isoformat() if job['created_at'] else None,
                        "updated_at": job['updated_at'].isoformat() if job['updated_at'] else None
                    })
                return result
        except Exception as e:
            raise DatabaseError(f"Failed to fetch scraping jobs: {str(e)}")