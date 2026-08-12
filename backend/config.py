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


settings = Settings()