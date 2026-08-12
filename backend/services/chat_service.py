from typing import List, Dict, Optional
from database import get_db_connection
from utils.helpers import generate_uuid, get_current_datetime, safe_datetime_for_db
from exceptions import NotFoundError, DatabaseError
from services.user_service import UserService
from services.project_service import ProjectService
from services.message_service import MessageService
from services.scraping_service import ScrapingService


class ChatService:
    """Service for chat operations"""
    
    @staticmethod
    async def create_chat(
        user_id: str,
        project_id: str,
        title: str = "New Chat"
    ) -> dict:
        """Create a new chat"""
        try:
            async with get_db_connection() as conn:
                chat_id = generate_uuid()
                now = get_current_datetime()
                safe_now = safe_datetime_for_db(now)
                
                await conn.execute(
                    """INSERT INTO pages 
                       (id, project_id, url, normalized_url, created_at, updated_at) 
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    chat_id, project_id, title, title.lower(), safe_now, safe_now
                )
                
                return {
                    "id": chat_id,
                    "user_id": user_id,
                    "project_id": project_id,
                    "title": title,
                    "created_at": safe_now.isoformat(),
                    "updated_at": safe_now.isoformat(),
                    "messages": []
                }
        except Exception as e:
            raise DatabaseError(f"Failed to create chat: {str(e)}")
    
    @staticmethod
    async def get_chats(user_id: Optional[str] = None) -> List[Dict]:
        """Get all chat sessions for a user"""
        if not user_id:
            user_id = await UserService.get_or_create_user()
        
        try:
            async with get_db_connection() as conn:
                # Get all pages for this user's projects
                pages = await conn.fetch(
                    """SELECT DISTINCT p.id, p.url, p.created_at, p.updated_at
                       FROM pages p
                       JOIN projects pr ON p.project_id = pr.id
                       WHERE pr.user_id = $1
                       ORDER BY p.updated_at DESC""",
                    user_id
                )
                
                result = []
                for page in pages:
                    # Get message count
                    msg_count = await conn.fetchval(
                        "SELECT COUNT(*) FROM messages WHERE chat_id = $1",
                        str(page['id'])
                    )
                    
                    # Get first message for title
                    first_msg = await conn.fetchrow(
                        """SELECT content FROM messages 
                           WHERE chat_id = $1 
                           ORDER BY created_at ASC 
                           LIMIT 1""",
                        str(page['id'])
                    )
                    
                    title = page['url'] if page['url'] else "Chat"
                    if first_msg and first_msg['content']:
                        content_preview = first_msg['content'][:40]
                        title = content_preview + "..." if len(content_preview) > 40 else content_preview
                    
                    result.append({
                        "id": str(page['id']),
                        "title": title,
                        "message_count": msg_count,
                        "created_at": page['created_at'].isoformat() if page['created_at'] else None,
                        "updated_at": page['updated_at'].isoformat() if page['updated_at'] else None
                    })
                
                return result
        except Exception as e:
            raise DatabaseError(f"Failed to fetch chats: {str(e)}")
    
    @staticmethod
    async def get_chat(chat_id: str, user_id: Optional[str] = None) -> Dict:
        """Get specific chat session with messages"""
        if not user_id:
            user_id = await UserService.get_or_create_user()
        
        try:
            async with get_db_connection() as conn:
                # Get the page
                page = await conn.fetchrow(
                    """SELECT p.id, p.url, p.created_at, p.updated_at
                       FROM pages p
                       JOIN projects pr ON p.project_id = pr.id
                       WHERE p.id = $1 AND pr.user_id = $2""",
                    chat_id, user_id
                )
                
                if not page:
                    raise NotFoundError("Chat not found")
                
                # Get messages
                messages = await MessageService.get_messages(chat_id, user_id)
                
                return {
                    "id": str(page['id']),
                    "user_id": user_id,
                    "title": page['url'] if page['url'] else "Chat",
                    "created_at": page['created_at'].isoformat() if page['created_at'] else None,
                    "updated_at": page['updated_at'].isoformat() if page['updated_at'] else None,
                    "messages": messages
                }
        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(f"Failed to fetch chat: {str(e)}")
    
    @staticmethod
    async def add_message_to_chat(
        chat_id: str,
        user_id: str,
        content: str,
        role: str,
        is_url: bool = False
    ) -> dict:
        """Add a message to a chat"""
        return await MessageService.create_message(
            chat_id=chat_id,
            user_id=user_id,
            role=role,
            content=content,
            is_url=is_url
        )
    
    @staticmethod
    async def delete_chat(chat_id: str, user_id: Optional[str] = None) -> bool:
        """Delete a chat session for a user"""
        if not user_id:
            user_id = await UserService.get_or_create_user()
        
        try:
            async with get_db_connection() as conn:
                # Check if page exists and belongs to user
                page = await conn.fetchrow(
                    """SELECT p.id
                       FROM pages p
                       JOIN projects pr ON p.project_id = pr.id
                       WHERE p.id = $1 AND pr.user_id = $2""",
                    chat_id, user_id
                )
                
                if not page:
                    raise NotFoundError("Chat not found")
                
                # Delete the page (cascade will delete messages, page_versions, etc.)
                await conn.execute(
                    "DELETE FROM pages WHERE id = $1",
                    chat_id
                )
                
                return True
        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(f"Failed to delete chat: {str(e)}")