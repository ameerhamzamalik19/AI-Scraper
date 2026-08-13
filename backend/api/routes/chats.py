from typing import Optional, List
from fastapi import APIRouter, Depends
from services.chat_service import ChatService
from api.deps import get_user_id_from_header
from models import ChatResponse
import logging
import os
from dotenv import load_dotenv

load_dotenv() 

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chats", tags=["chats"])

@router.get("", response_model=List[ChatResponse])
async def get_chats(
    user_id: Optional[str] = Depends(get_user_id_from_header)
):
    """Get all chat sessions for a user"""
    print(f"API call to get chats for user_id: {user_id}")
    user_id = os.getenv("HARDCODED_USER_ID")  # Hardcoded for testing
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
    await ChatService.delete_chat(chat_id, user_id)
    return {"message": "Chat deleted successfully"}