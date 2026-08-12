from typing import Optional
from fastapi import Header, HTTPException, status


async def get_user_id_from_header(
    x_user_id: Optional[str] = Header(None, alias="X-User-ID")
) -> Optional[str]:
    """Extract user ID from header"""
    return x_user_id


async def get_chat_id_from_query(
    chat_id: Optional[str] = None
) -> Optional[str]:
    """Extract chat ID from query parameter"""
    return chat_id