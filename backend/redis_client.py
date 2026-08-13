import json
import redis
from typing import Optional
from datetime import datetime, timezone
from config import settings
from exceptions import RedisError
from utils.helpers import generate_uuid, get_iso_timestamp


class RedisClient:
    """Redis client wrapper"""
    
    def __init__(self):
        self.client: Optional[redis.Redis] = None
        self._connect()
    
    def _connect(self):
        """Connect to Redis"""
        try:
            if settings.REDIS_URL:
                self.client = redis.from_url(settings.REDIS_URL, decode_responses=True)
                self.client.ping()
                print("Redis connected successfully")
            else:
                print("Redis URL not configured, running without Redis")
        except Exception as e:
            print(f"Redis connection failed: {e}")
            self.client = None
    
    def is_available(self) -> bool:
        """Check if Redis is available"""
        return self.client is not None
    
    def add_scraping_job(
            self, url: str, 
            project_id: str, 
            user_id: str,
            chat_id: str = None,
            message_id: str = None,
            page_id: str = None 
        ) -> Optional[str]:
        """Add a URL scraping job to Redis queue"""
        if not self.is_available():
            print("Redis client not available, skipping job queue")
            return None
        
        try:
            job_id = generate_uuid()
            job_data = {
                "job_id": job_id,
                "url": url,
                "project_id": project_id,
                "user_id": user_id,
                "chat_id": chat_id,
                "message_id": message_id,
                "page_id": page_id,
                "status": "pending",
                "created_at": get_iso_timestamp()
            }
            
            # Store job in Redis with TTL
            redis_key = f"{settings.REDIS_JOB_PREFIX}{job_id}"
            self.client.setex(redis_key, settings.REDIS_JOB_TTL, json.dumps(job_data))
            
            # Add to queue
            self.client.lpush(settings.SCRAPING_QUEUE_NAME, json.dumps(job_data))
            
            print(f"Added scraping job {job_id} to Redis queue for URL: {url}")
            return job_id
        except Exception as e:
            print(f"Failed to add scraping job to Redis: {e}")
            return None
    
    def get_job(self, job_id: str) -> Optional[dict]:
        """Get job from Redis"""
        if not self.is_available():
            return None
        
        try:
            data = self.client.get(f"{settings.REDIS_JOB_PREFIX}{job_id}")
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            print(f"Failed to get job from Redis: {e}")
            return None

    def get_queue_length(self) -> int:
        """Get the number of jobs in the queue"""
        if not self.is_available():
            return 0
        
        try:
            return self.client.llen(settings.SCRAPING_QUEUE_NAME)
        except Exception as e:
            print(f"Failed to get queue length: {e}")
            return 0


# Singleton instance
redis_client = RedisClient()