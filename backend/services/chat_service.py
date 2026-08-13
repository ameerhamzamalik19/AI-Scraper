from typing import List, Optional
from database import get_db_connection
from services.message_service import MessageService
from utils.helpers import generate_uuid, get_current_datetime, safe_datetime_for_db
from exceptions import DatabaseError, NotFoundError
import logging

logger = logging.getLogger(__name__)

class ChatService:
    """Service for chat session operations"""
    
    @staticmethod
    async def create_chat(
        user_id: str,
        project_id: Optional[str] = None,
        title: Optional[str] = None
    ) -> dict:
        """Create a new chat session with optional project association"""
        try:
            async with get_db_connection() as conn:
                chat_id = generate_uuid()
                now = get_current_datetime()
                safe_now = safe_datetime_for_db(now)
                
                if not title:
                    title = f"Chat {now.strftime('%Y-%m-%d %H:%M')}"
                
                await conn.execute(
                    """INSERT INTO chats 
                       (id, user_id, project_id, title, created_at, updated_at) 
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    chat_id, user_id, project_id, title, safe_now, safe_now
                )
                
                return {
                    "id": chat_id,
                    "user_id": user_id,
                    "project_id": project_id,
                    "title": title,
                    "created_at": safe_now.isoformat(),
                    "updated_at": safe_now.isoformat()
                }
        except Exception as e:
            raise DatabaseError(f"Failed to create chat: {str(e)}")
    
    @staticmethod
    async def get_chats(user_id: str) -> List[dict]:
        """Get all chats for a user, ordered by most recent message"""
        try:
            logger.info(f"Fetching chats for user_id: {user_id}")
            async with get_db_connection() as conn:
                chats = await conn.fetch(
                    """SELECT 
                        c.id, 
                        c.user_id, 
                        c.project_id, 
                        c.title, 
                        c.created_at, 
                        c.updated_at,
                        COUNT(m.id) as message_count,
                        MAX(m.created_at) as last_message_at
                       FROM chats c
                       LEFT JOIN messages m ON m.chat_id = c.id
                       WHERE c.user_id = $1
                       GROUP BY c.id
                       ORDER BY last_message_at DESC NULLS LAST, c.updated_at DESC""",
                    user_id
                )
                
                return [{
                    "id": str(chat['id']),
                    "user_id": str(chat['user_id']),
                    "project_id": str(chat['project_id']) if chat['project_id'] else None,
                    "title": chat['title'],
                    "message_count": chat['message_count'],
                    "last_message_at": chat['last_message_at'].isoformat() if chat['last_message_at'] else None,
                    "created_at": chat['created_at'].isoformat() if chat['created_at'] else None,
                    "updated_at": chat['updated_at'].isoformat() if chat['updated_at'] else None
                } for chat in chats]
        except Exception as e:
            raise DatabaseError(f"Failed to get chats: {str(e)}")
    
    @staticmethod
    async def get_chat(chat_id: str, user_id: str) -> dict:
        """Get a specific chat with messages"""
        try:
            async with get_db_connection() as conn:
                chat = await conn.fetchrow(
                    """SELECT 
                        c.id, 
                        c.user_id, 
                        c.project_id, 
                        c.title, 
                        c.created_at, 
                        c.updated_at,
                        COUNT(m.id) as message_count,
                        MAX(m.created_at) as last_message_at
                       FROM chats c
                       LEFT JOIN messages m ON m.chat_id = c.id
                       WHERE c.id = $1 AND c.user_id = $2
                       GROUP BY c.id""",
                    chat_id, user_id
                )
                
                if not chat:
                    raise NotFoundError(f"Chat {chat_id} not found")
                
                # Get messages
                messages = await MessageService.get_messages(chat_id, user_id)
                
                return {
                    "id": str(chat['id']),
                    "user_id": str(chat['user_id']),
                    "project_id": str(chat['project_id']) if chat['project_id'] else None,
                    "title": chat['title'],
                    "message_count": chat['message_count'],
                    "last_message_at": chat['last_message_at'].isoformat() if chat['last_message_at'] else None,
                    "messages": messages,
                    "created_at": chat['created_at'].isoformat() if chat['created_at'] else None,
                    "updated_at": chat['updated_at'].isoformat() if chat['updated_at'] else None
                }
        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(f"Failed to get chat: {str(e)}")
    
    @staticmethod
    async def delete_chat(chat_id: str, user_id: str) -> bool:
        """Delete a chat and all its messages"""
        try:
            async with get_db_connection() as conn:
                # Verify ownership first
                chat = await conn.fetchrow(
                    "SELECT id FROM chats WHERE id = $1 AND user_id = $2",
                    chat_id, user_id
                )
                if not chat:
                    raise NotFoundError(f"Chat {chat_id} not found")
                
                # Delete (messages will cascade due to ON DELETE CASCADE)
                result = await conn.execute(
                    "DELETE FROM chats WHERE id = $1 AND user_id = $2",
                    chat_id, user_id
                )
                return result != "DELETE 0"
        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(f"Failed to delete chat: {str(e)}")
    
    @staticmethod
    async def add_message_to_chat(
        chat_id: str,
        user_id: str,
        content: str,
        role: str,
        is_url: bool = False
    ) -> dict:
        """Add a message to a chat (wrapper for MessageService)"""
        return await MessageService.create_message(
            chat_id=chat_id,
            user_id=user_id,
            role=role,
            content=content,
            is_url=is_url
        )