from typing import Optional, Dict, Any, List
from database import get_db_connection
from utils.helpers import generate_uuid, get_current_datetime, safe_datetime_for_db
from exceptions import DatabaseError, NotFoundError


class PageService:
    """Service for page/scraping operations"""
    
    @staticmethod
    async def create_page_for_chat(
        chat_id: str,
        project_id: str,
        url: str,
        normalized_url: str
    ) -> dict:
        """Always create a new page for a chat (each chat gets its own page)"""
        try:
            print(f"📄 Creating new page for chat: {chat_id}, URL: {url}")
            
            async with get_db_connection() as conn:
                page_id = generate_uuid()
                now = get_current_datetime()
                safe_now = safe_datetime_for_db(now)
                
                # Always insert new page - no duplicate checking
                await conn.execute(
                    """INSERT INTO pages 
                       (id, chat_id, project_id, url, normalized_url, created_at, updated_at) 
                       VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                    page_id, chat_id, project_id, url, normalized_url, safe_now, safe_now
                )
                
                print(f"✅ Created new page: {page_id}")
                
                return {
                    "id": page_id,
                    "chat_id": chat_id,
                    "project_id": project_id,
                    "url": url,
                    "normalized_url": normalized_url,
                    "created_at": safe_now.isoformat(),
                    "updated_at": safe_now.isoformat()
                }
        except Exception as e:
            print(f"❌ Failed to create page: {str(e)}")
            import traceback
            traceback.print_exc()
            raise DatabaseError(f"Failed to create page: {str(e)}")
    
    @staticmethod
    async def create_page_version(
        page_id: str,
        status_code: int = None,
        content_type: str = None,
        content_hash: str = None,
        fetch_method: str = 'httpx',
        response_size: int = 0
    ) -> dict:
        """Create a new version of a page"""
        try:
            async with get_db_connection() as conn:
                version_id = generate_uuid()
                now = get_current_datetime()
                safe_now = safe_datetime_for_db(now)
                
                await conn.execute(
                    """INSERT INTO page_versions 
                       (id, page_id, status_code, content_type, content_hash, 
                        fetch_method, response_size, fetched_at, created_at, updated_at) 
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
                    version_id, page_id, status_code, content_type, content_hash,
                    fetch_method, response_size, safe_now, safe_now, safe_now
                )
                
                return {
                    "id": version_id,
                    "page_id": page_id,
                    "status_code": status_code,
                    "content_type": content_type,
                    "content_hash": content_hash,
                    "fetch_method": fetch_method,
                    "response_size": response_size,
                    "fetched_at": safe_now.isoformat(),
                    "created_at": safe_now.isoformat(),
                    "updated_at": safe_now.isoformat()
                }
        except Exception as e:
            raise DatabaseError(f"Failed to create page version: {str(e)}")