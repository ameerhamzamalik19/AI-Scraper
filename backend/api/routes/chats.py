from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from services.chat_service import ChatService
from api.deps import get_user_id_from_header
from models import ChatResponse
import logging
import os
from dotenv import load_dotenv
from utils.chat_status_tracker import ChatStatusTracker

load_dotenv() 

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chats", tags=["chats"])

@router.get("", response_model=List[ChatResponse])
async def get_chats(
    user_id: Optional[str] = Depends(get_user_id_from_header)
):
    """Get all chat sessions for a user"""
    user_id = os.getenv("HARDCODED_USER_ID")  # Hardcoded for testing
    print(f"API call to get chats for user_id: {user_id}")
    return await ChatService.get_chats(user_id)


@router.get("/{chat_id}", response_model=ChatResponse)
async def get_chat(
    chat_id: str,
    user_id: Optional[str] = Depends(get_user_id_from_header)
):
    """Get specific chat session for a user"""
    user_id = os.getenv("HARDCODED_USER_ID")
    return await ChatService.get_chat(chat_id, user_id)


@router.delete("/{chat_id}")
async def delete_chat(
    chat_id: str,
    user_id: Optional[str] = Depends(get_user_id_from_header)
):
    """Delete a chat session for a user"""
    user_id = os.getenv("HARDCODED_USER_ID")
    await ChatService.delete_chat(chat_id, user_id)
    return {"message": "Chat deleted successfully"}

@router.get("/{chat_id}/crawled-urls")
async def get_crawled_urls(chat_id: str):
    """Get all crawled URLs for a chat."""
    from database_sync import execute_query
    
    result = execute_query(
        """
        SELECT 
            url,
            page_title,
            status,
            error_message,
            crawled_at
        FROM crawled_urls
        WHERE chat_id = %s
        ORDER BY crawled_at ASC
        """,
        (chat_id,)
    )
    
    return {
        'chat_id': chat_id,
        'total': len(result),
        'completed': sum(1 for r in result if r['status'] == 'completed'),
        'failed': sum(1 for r in result if r['status'] == 'failed'),
        'pending': sum(1 for r in result if r['status'] == 'pending'),
        'processing': sum(1 for r in result if r['status'] == 'processing'),
        'urls': [
            {
                'url': r['url'],
                'title': r['page_title'] or r['url'],
                'status': r['status'],
                'error': r['error_message'],
                'crawled_at': r['crawled_at']
            }
            for r in result
        ]
    }

@router.get("/{chat_id}/status")
async def get_chat_status(chat_id: str):
    """
    Get the processing status of a chat.
    Returns the current status, progress, and step information.
    """
    from database_sync import execute_one
    
    # First check if chat exists
    chat = execute_one(
        "SELECT id FROM chats WHERE id = %s",
        (chat_id,)
    )
    
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    # Get progress summary from ChatStatusTracker
    status = ChatStatusTracker.get_progress_summary(chat_id)
    
    if not status:
        # If no status found, return default
        return {
            'chat_id': chat_id,
            'exists': True,
            'status': 'pending',
            'progress': 0,
            'current_step': 'Initializing...',
            'friendly_message': 'Waiting to start...',
            'document_id': None,
            'started_at': None,
            'completed_at': None,
            'error_message': None,
            'is_ready': False,
            'is_processing': True,
            'is_failed': False,
            'has_error': False
        }
    
    return status