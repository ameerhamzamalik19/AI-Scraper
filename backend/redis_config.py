import os
import redis

# Get Redis configuration from environment variables
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))

# Also support REDIS_URL if provided
REDIS_URL = os.getenv("REDIS_URL", None)

if REDIS_URL:
    redis_client = redis.Redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_timeout=5.0
    )
else:
    redis_client = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        decode_responses=True,
        socket_timeout=5.0
    )

REDIS_JOB_PREFIX: str = "scraping_job:"
SCRAPING_QUEUE_NAME: str = "scraping_queue"
PROCESSING_QUEUE_NAME = "processing_queue"
CHUNKING_QUEUE_NAME: str = "chunking_queue"     
EMBEDDING_QUEUE_NAME: str = "embedding_queue"

JOB_TTL_SECONDS: int = 86000