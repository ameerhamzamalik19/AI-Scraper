from typing import Dict, Optional, Tuple
from database import get_db_connection
from utils.helpers import generate_uuid, get_current_datetime, safe_datetime_for_db
from utils.detectors import InputDetector
from exceptions import DatabaseError
from services.user_service import UserService
from services.project_service import ProjectService
from services.scraping_service import ScrapingService


class PageService:
    """Service for page operations"""
    
    @staticmethod
    async def create_or_update_page(
        chat_id: Optional[str],
        project_id: str,
        content: str,
        detection: Dict
    ) -> Tuple[str, bool]:
        """Create or update a page"""
        is_new_page = False
        
        if not chat_id:
            chat_id = generate_uuid()
            is_new_page = True
            print(f"Created new page with UUID: {chat_id}")
        
        now = get_current_datetime()
        safe_now = safe_datetime_for_db(now)
        
        try:
            async with get_db_connection() as conn:
                # Create page if it doesn't exist
                if is_new_page:
                    page_url = content if detection['has_url'] and detection['urls'] else f"message_{chat_id[:8]}"
                    normalized_url = page_url.lower().strip()
                    await conn.execute(
                        """INSERT INTO pages (id, project_id, url, normalized_url, created_at, updated_at) 
                           VALUES ($1, $2, $3, $4, $5, $6)""",
                        chat_id, project_id, page_url, normalized_url, safe_now, safe_now
                    )
                else:
                    # Update page timestamp
                    await conn.execute(
                        "UPDATE pages SET updated_at = $1 WHERE id = $2",
                        safe_now, chat_id
                    )
                
                return chat_id, is_new_page
        except Exception as e:
            raise DatabaseError(f"Failed to create/update page: {str(e)}")
    
    @staticmethod
    async def create_page_version_and_document(
        chat_id: str,
        content: str,
        response_content: str,
        detection: Dict,
        project_id: str,
        user_id: str
    ) -> Tuple[str, str, Optional[str]]:
        """Create page version and document"""
        now = get_current_datetime()
        safe_now = safe_datetime_for_db(now)
        
        page_version_id = generate_uuid()
        document_id = generate_uuid()
        scraping_job_id = None
        
        try:
            async with get_db_connection() as conn:
                # Create page version with proper datetime
                await conn.execute(
                    """INSERT INTO page_versions 
                       (id, page_id, fetch_method, processing_status, fetched_at, created_at, updated_at) 
                       VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                    page_version_id, chat_id, 'httpx', 'PENDING', safe_now, safe_now, safe_now
                )
                
                # Create document with combined user + assistant content
                document_content = f"User: {content}\n\nAssistant: {response_content}"
                await conn.execute(
                    """INSERT INTO documents 
                       (id, page_version_id, content, content_format, processing_status, created_at, updated_at) 
                       VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                    document_id, page_version_id, document_content, 
                    'markdown', 'COMPLETED', safe_now, safe_now
                )
                
                # If URL detected, add to Redis queue for scraping
                if detection['has_url']:
                    scraping_job_id = await ScrapingService.create_scraping_jobs(
                        detection['urls'], project_id, user_id, conn
                    )
                
                return document_id, page_version_id, scraping_job_id
        except Exception as e:
            raise DatabaseError(f"Failed to create page version: {str(e)}")


    @staticmethod
    async def create_page_from_url(
        url: str,
        project_id: str,
        user_id: str
    ) -> Tuple[str, str, str]:
        """Create a page from a URL for scraping"""
        now = get_current_datetime()
        safe_now = safe_datetime_for_db(now)
        
        chat_id = generate_uuid()
        page_version_id = generate_uuid()
        document_id = generate_uuid()
        
        try:
            async with get_db_connection() as conn:
                # Create page
                normalized_url = url.lower().strip()
                await conn.execute(
                    """INSERT INTO pages (id, project_id, url, normalized_url, created_at, updated_at) 
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    chat_id, project_id, url, normalized_url, safe_now, safe_now
                )
                
                # Create page version
                await conn.execute(
                    """INSERT INTO page_versions 
                       (id, page_id, fetch_method, processing_status, fetched_at, created_at, updated_at) 
                       VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                    page_version_id, chat_id, 'httpx', 'PENDING', safe_now, safe_now, safe_now
                )
                
                # Create document (will be populated after scraping)
                await conn.execute(
                    """INSERT INTO documents 
                       (id, page_version_id, content, content_format, processing_status, created_at, updated_at) 
                       VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                    document_id, page_version_id, '', 'markdown', 'PENDING', safe_now, safe_now
                )
                
                # Add to scraping queue
                await ScrapingService.create_scraping_jobs(
                    [url], project_id, user_id, conn
                )
                
                return chat_id, page_version_id, document_id
        except Exception as e:
            raise DatabaseError(f"Failed to create page: {str(e)}")