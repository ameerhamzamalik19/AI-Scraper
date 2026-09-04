import json
import redis
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from config import settings
from exceptions import RedisError
from redis_config import JOB_TTL_SECONDS
from utils.helpers import generate_uuid, get_iso_timestamp
import logging

logger = logging.getLogger(__name__)

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
    
    # ============================================================
    # Job Queue Methods
    # ============================================================
    
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

            from workers.crawler_worker import crawl_website
            crawl_website.send(job_id)
            
            print(f"Added scraping job {job_id} to Redis queue for URL: {url}")
            return job_id
        except Exception as e:
            print(f"Failed to add scraping job to Redis: {e}")
            return None

    def update_job_status(self, job_id: str, status: str, **kwargs) -> bool:
        """Update job status in Redis"""
        if not self.is_available():
            return False
        
        try:
            redis_key = f"{settings.REDIS_JOB_PREFIX}{job_id}"
            data = self.client.get(redis_key)
            
            if not data:
                print(f"⚠️ Job {job_id} not found in Redis")
                return False
            
            job_data = json.loads(data)
            job_data['status'] = status
            job_data.update(kwargs)
            job_data['updated_at'] = get_iso_timestamp()
            
            self.client.setex(redis_key, settings.REDIS_JOB_TTL, json.dumps(job_data))
            print(f"✅ Updated job {job_id} status to: {status}")
            return True
        except Exception as e:
            print(f"❌ Failed to update job status: {e}")
            return False
    
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

    # ============================================================
    # Pub/Sub Methods (for WebSocket broadcasting)
    # ============================================================
    
    def publish(self, channel: str, message: str) -> int:
        """Publish a message to a Redis channel."""
        if not self.is_available():
            logger.warning("Redis not available, cannot publish")
            return 0
        try:
            return self.client.publish(channel, message)
        except Exception as e:
            logger.error(f"Failed to publish to Redis: {e}")
            return 0

    def pubsub(self):
        """Get a Redis pubsub object."""
        if not self.is_available():
            logger.warning("Redis not available, cannot create pubsub")
            return None
        try:
            return self.client.pubsub()
        except Exception as e:
            logger.error(f"Failed to create pubsub: {e}")
            return None

    # ============================================================
    # Boilerplate Cache Methods
    # ============================================================
    
    def set_boilerplate_patterns(self, domain: str, patterns: List[str]) -> bool:
        """Store boilerplate patterns for a domain."""
        if not self.is_available():
            return False
        try:
            key = f"boilerplate_patterns:{domain}"
            self.client.set(key, json.dumps(patterns))
            logger.debug(f"💾 Stored {len(patterns)} patterns for {domain}")
            return True
        except Exception as e:
            logger.error(f"Failed to store boilerplate patterns: {e}")
            return False
    
    def get_boilerplate_patterns(self, domain: str) -> Optional[List[str]]:
        """Get boilerplate patterns for a domain."""
        if not self.is_available():
            return None
        try:
            key = f"boilerplate_patterns:{domain}"
            data = self.client.get(key)
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            logger.error(f"Failed to get boilerplate patterns: {e}")
            return None
    
    def delete_boilerplate_patterns(self, domain: Optional[str] = None) -> bool:
        """Delete boilerplate patterns for a domain or all domains."""
        if not self.is_available():
            return False
        try:
            if domain:
                key = f"boilerplate_patterns:{domain}"
                self.client.delete(key)
                logger.info(f"🧹 Deleted boilerplate patterns for {domain}")
            else:
                # Delete all boilerplate patterns
                keys = self.client.keys("boilerplate_patterns:*")
                if keys:
                    self.client.delete(*keys)
                logger.info("🧹 Deleted all boilerplate patterns")
            return True
        except Exception as e:
            logger.error(f"Failed to delete boilerplate patterns: {e}")
            return False
    
    def set_processed_domain(self, domain: str) -> bool:
        """Mark a domain as processed (first page learned)."""
        if not self.is_available():
            return False
        try:
            self.client.sadd("processed_domains", domain)
            logger.debug(f"✅ Marked {domain} as processed")
            return True
        except Exception as e:
            logger.error(f"Failed to mark domain as processed: {e}")
            return False
    
    def get_processed_domains(self) -> set:
        """Get all processed domains."""
        if not self.is_available():
            return set()
        try:
            return self.client.smembers("processed_domains")
        except Exception as e:
            logger.error(f"Failed to get processed domains: {e}")
            return set()
    
    def is_domain_processed(self, domain: str) -> bool:
        """Check if a domain has been processed."""
        if not self.is_available():
            return False
        try:
            return self.client.sismember("processed_domains", domain)
        except Exception as e:
            logger.error(f"Failed to check processed domain: {e}")
            return False

    # ============================================================
    # General Key-Value Methods
    # ============================================================
    
    def get(self, key: str) -> Optional[str]:
        """Get a value from Redis."""
        if not self.is_available():
            return None
        try:
            return self.client.get(key)
        except Exception as e:
            logger.error(f"Redis GET error: {e}")
            return None
    
    def set(self, key: str, value: str, ex: int = None) -> bool:
        """Set a value in Redis."""
        if not self.is_available():
            return False
        try:
            self.client.set(key, value, ex=ex)
            return True
        except Exception as e:
            logger.error(f"Redis SET error: {e}")
            return False
    
    def delete(self, *keys: str) -> int:
        """Delete keys from Redis."""
        if not self.is_available():
            return 0
        try:
            return self.client.delete(*keys)
        except Exception as e:
            logger.error(f"Redis DELETE error: {e}")
            return 0
    
    def keys(self, pattern: str) -> List[str]:
        """Get keys matching a pattern."""
        if not self.is_available():
            return []
        try:
            return self.client.keys(pattern)
        except Exception as e:
            logger.error(f"Redis KEYS error: {e}")
            return []
    
    def exists(self, key: str) -> bool:
        """Check if a key exists in Redis."""
        if not self.is_available():
            return False
        try:
            return self.client.exists(key) > 0
        except Exception as e:
            logger.error(f"Redis EXISTS error: {e}")
            return False

# Singleton instance
redis_client = RedisClient()