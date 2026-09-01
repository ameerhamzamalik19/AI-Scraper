import os
from typing import Optional
from dotenv import load_dotenv
from redis_config import REDIS_JOB_PREFIX, JOB_TTL_SECONDS

load_dotenv()

class Settings:
    """Application settings"""
    
    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", 
        "postgresql://user:password@localhost:5432/universal_scraper"
    )
    
    # Redis
    REDIS_URL: Optional[str] = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    REDIS_JOB_PREFIX: str = REDIS_JOB_PREFIX
    REDIS_JOB_TTL: int = JOB_TTL_SECONDS  # 7 days
    
    # CORS
    CORS_ORIGINS: list = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
    
    # Default user
    DEFAULT_USER_EMAIL: str = os.getenv("DEFAULT_USER_EMAIL", "test@example.com")
    DEFAULT_PROJECT_NAME: str = os.getenv("DEFAULT_PROJECT_NAME", "Default Project")
    
    # Pagination
    DEFAULT_PAGE_SIZE: int = int(os.getenv("DEFAULT_PAGE_SIZE", "50"))
    
    # Scraping
    SCRAPING_QUEUE_NAME: str = os.getenv("SCRAPING_QUEUE_NAME", "scraping_queue")


class CrawlerSettings:
    """Crawler configuration"""
    
    # Limits
    MAX_PAGES_PER_CRAWL: int = 5
    MAX_CRAWL_DEPTH: int = 3  # Not used with BFS, but kept for reference
    MAX_RESPONSE_SIZE: int = 10 * 1024 * 1024  # 10MB
    
    # Timeouts
    REQUEST_TIMEOUT: int = 30  # seconds
    BROWSER_TIMEOUT: int = 60  # seconds
    
    # Politeness (even without robots.txt)
    REQUEST_DELAY: float = 1.0  # seconds between requests to same domain
    
    # User Agent
    USER_AGENT: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    
    # Retry
    MAX_RETRIES: int = 3
    RETRY_DELAY: int = 2  # seconds


crawler_settings = CrawlerSettings()
settings = Settings()