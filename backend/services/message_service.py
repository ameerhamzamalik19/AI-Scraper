from typing import List, Optional
from database import get_db_connection
from utils.helpers import generate_uuid, get_current_datetime, safe_datetime_for_db
from exceptions import DatabaseError, NotFoundError


class MessageService:
    """Service for chat message operations"""
    
    @staticmethod
    async def create_message(
        chat_id: str,
        user_id: str,
        role: str,
        content: str,
        is_url: bool = False
    ) -> dict:
        """Create a new message in a chat"""
        try:
            async with get_db_connection() as conn:
                # Verify chat exists and belongs to user
                chat = await conn.fetchrow(
                    "SELECT id FROM chats WHERE id = $1 AND user_id = $2",
                    chat_id, user_id
                )
                if not chat:
                    raise NotFoundError(f"Chat {chat_id} not found")
                
                message_id = generate_uuid()
                now = get_current_datetime()
                safe_now = safe_datetime_for_db(now)
                
                await conn.execute(
                    """INSERT INTO messages 
                       (id, chat_id, user_id, role, content, is_url, url_processed, created_at, updated_at) 
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                    message_id, chat_id, user_id, role, content, is_url, False, safe_now, safe_now
                )
                
                # Update chat updated_at
                await conn.execute(
                    "UPDATE chats SET updated_at = $1 WHERE id = $2",
                    safe_now, chat_id
                )
                
                return {
                    "id": message_id,
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "role": role,
                    "content": content,
                    "is_url": is_url,
                    "url_processed": False,
                    "timestamp": safe_now.isoformat(),  # ← ADDED
                    "created_at": safe_now.isoformat(),
                    "updated_at": safe_now.isoformat()
                }
        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(f"Failed to create message: {str(e)}")
    
    @staticmethod
    async def get_messages(
        chat_id: str, 
        user_id: str, 
        limit: int = 50,
        offset: int = 0
    ) -> List[dict]:
        """Get messages for a chat with pagination"""
        try:
            async with get_db_connection() as conn:
                # Verify chat exists and belongs to user
                chat = await conn.fetchrow(
                    "SELECT id FROM chats WHERE id = $1 AND user_id = $2",
                    chat_id, user_id
                )
                if not chat:
                    raise NotFoundError(f"Chat {chat_id} not found")
                
                messages = await conn.fetch(
                    """SELECT id, chat_id, user_id, role, content, is_url, url_processed, created_at, updated_at
                       FROM messages 
                       WHERE chat_id = $1 
                       ORDER BY created_at ASC
                       LIMIT $2 OFFSET $3""",
                    chat_id, limit, offset
                )
                
                return [{
                    "id": str(msg['id']),
                    "chat_id": str(msg['chat_id']),
                    "user_id": str(msg['user_id']),
                    "role": msg['role'],
                    "content": msg['content'],
                    "is_url": msg['is_url'],
                    "url_processed": msg['url_processed'],
                    "timestamp": msg['created_at'].isoformat() if msg['created_at'] else None,  # ← ADDED
                    "created_at": msg['created_at'].isoformat() if msg['created_at'] else None,
                    "updated_at": msg['updated_at'].isoformat() if msg['updated_at'] else None
                } for msg in messages]
        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(f"Failed to get messages: {str(e)}")
    
    @staticmethod
    async def get_last_message(chat_id: str, user_id: str) -> Optional[dict]:
        """Get the last message in a chat"""
        try:
            async with get_db_connection() as conn:
                # Verify chat exists and belongs to user
                chat = await conn.fetchrow(
                    "SELECT id FROM chats WHERE id = $1 AND user_id = $2",
                    chat_id, user_id
                )
                if not chat:
                    raise NotFoundError(f"Chat {chat_id} not found")
                
                message = await conn.fetchrow(
                    """SELECT id, chat_id, user_id, role, content, is_url, url_processed, created_at, updated_at
                       FROM messages 
                       WHERE chat_id = $1 
                       ORDER BY created_at DESC
                       LIMIT 1""",
                    chat_id
                )
                
                if not message:
                    return None
                
                return {
                    "id": str(message['id']),
                    "chat_id": str(message['chat_id']),
                    "user_id": str(message['user_id']),
                    "role": message['role'],
                    "content": message['content'],
                    "is_url": message['is_url'],
                    "url_processed": message['url_processed'],
                    "timestamp": message['created_at'].isoformat() if message['created_at'] else None,  # ← ADDED
                    "created_at": message['created_at'].isoformat() if message['created_at'] else None,
                    "updated_at": message['updated_at'].isoformat() if message['updated_at'] else None
                }
        except NotFoundError:
            raise
        except Exception as e:
            raise DatabaseError(f"Failed to get last message: {str(e)}")
    
    @staticmethod
    async def update_url_processed(message_id: str) -> bool:
        """Mark a URL as processed"""
        try:
            async with get_db_connection() as conn:
                result = await conn.execute(
                    "UPDATE messages SET url_processed = TRUE WHERE id = $1",
                    message_id
                )
                return result != "UPDATE 0"
        except Exception as e:
            raise DatabaseError(f"Failed to update message: {str(e)}")