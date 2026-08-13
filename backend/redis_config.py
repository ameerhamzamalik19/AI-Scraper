import redis

redis_client: redis.Redis = redis.Redis(
    host='localhost', 
    port=6379, 
    db=0, 
    decode_responses=True,
    socket_timeout=5.0  # Prevent infinite hangs if Redis dies
)

REDIS_JOB_PREFIX: str = "scraping_job:"

JOB_TTL_SECONDS: int = 86000