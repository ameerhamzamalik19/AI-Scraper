from typing import Optional, Dict, Any
import hashlib
from database import get_db_connection
from utils.helpers import generate_uuid, get_current_datetime, safe_datetime_for_db
from exceptions import DatabaseError


class CrawlerStorage:
    """Store crawled data in PostgreSQL"""
    
    @staticmethod
    async def get_or_create_page(
        project_id: str,
        chat_id: str,
        url: str,
        normalized_url: str
    ) -> Dict[str, Any]:
        """
        Get existing page or create new one.
        Since we dropped the unique constraint, we always create a new page.
        """
        # Always create a new page for this crawl
        # This way each crawl gets its own page record
        page_id = generate_uuid()
        now = get_current_datetime()
        safe_now = safe_datetime_for_db(now)
        
        async with get_db_connection() as conn:
            await conn.execute(
                """INSERT INTO pages 
                   (id, chat_id, project_id, url, normalized_url, created_at, updated_at) 
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                page_id, chat_id, project_id, url, normalized_url, safe_now, safe_now
            )
            
            return {
                "id": page_id,
                "chat_id": chat_id,
                "project_id": project_id,
                "url": url,
                "normalized_url": normalized_url,
                "created_at": safe_now.isoformat(),
                "updated_at": safe_now.isoformat()
            }
    
    @staticmethod
    async def create_page_version(
        page_id: str,
        url: str,
        html: str,
        metadata: Dict[str, Any],
        status_code: int,
        content_type: str,
        response_size: int,
        fetch_method: str
    ) -> Dict[str, Any]:
        """Create a new page version with raw HTML"""
        version_id = generate_uuid()
        now = get_current_datetime()
        safe_now = safe_datetime_for_db(now)
        
        # Create content hash
        content_hash = hashlib.sha256(html.encode('utf-8')).hexdigest()
        
        async with get_db_connection() as conn:
            await conn.execute(
                """INSERT INTO page_versions 
                   (id, page_id, status_code, content_type, 
                    content_hash, fetch_method, response_size, 
                    fetched_at, created_at, updated_at) 
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
                version_id, page_id, status_code, content_type,
                content_hash, fetch_method, response_size,
                safe_now, safe_now, safe_now
            )
            
            # Also create document entry for the raw HTML
            # This will be processed by the processor worker later
            document_id = generate_uuid()
            await conn.execute(
                """INSERT INTO documents 
                   (id, page_version_id, content, content_format, 
                    metadata, processing_status, created_at, updated_at) 
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
                document_id, version_id, html, 'html',
                metadata, 'PENDING', safe_now, safe_now
            )
            
            return {
                "version_id": version_id,
                "page_id": page_id,
                "document_id": document_id,
                "content_hash": content_hash,
                "created_at": safe_now.isoformat()
            }