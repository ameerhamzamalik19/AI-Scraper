import json
from typing import Optional, Dict, Any
import hashlib
from database import get_db_connection
from utils.helpers import generate_uuid, get_current_datetime, safe_datetime_for_db
from exceptions import DatabaseError
import logging

logger = logging.getLogger(__name__)

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
            logger.info(f"Creating new page: {page_id}")
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
        
        # ============================================================
        # FIX: Sanitize HTML to remove null bytes and invalid UTF-8
        # ============================================================
        if html:
            # Remove null bytes
            html = html.replace('\x00', '')
            # Remove other control characters except newline, tab, carriage return
            import re
            html = re.sub(r'[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]', '', html)
            # Ensure valid UTF-8
            try:
                html = html.encode('utf-8', errors='ignore').decode('utf-8')
            except Exception:
                # If all else fails, force clean
                html = ''.join(c for c in html if ord(c) >= 32 or c in '\n\r\t')
        
        # Create content hash from sanitized HTML
        content_hash = hashlib.sha256(html.encode('utf-8')).hexdigest()
        
        async with get_db_connection() as conn:
            try:
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
                logger.info(f"Creating new page version: {version_id}")
            except Exception as e:
                logger.error(f"Error creating page version: {e}")
                raise DatabaseError(f"Failed to create page version: {e}")
            
            # Also create document entry for the raw HTML
            # This will be processed by the processor worker later
            document_id = generate_uuid()
            metadata_json = json.dumps(metadata) if metadata else '{}'
            try:
                await conn.execute(
                    """INSERT INTO documents 
                    (id, page_version_id, content, content_format, 
                        metadata, processing_status, created_at, updated_at) 
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
                    document_id, version_id, html, 'html',  # Now sanitized
                    metadata_json, 'PENDING', safe_now, safe_now
                )
            except Exception as e:
                logger.error(f"Error creating document: {e}")
                raise DatabaseError(f"Failed to create document: {e}")
            
            return {
                "version_id": version_id,
                "page_id": page_id,
                "document_id": document_id,
                "content_hash": content_hash,
                "created_at": safe_now.isoformat()
            }