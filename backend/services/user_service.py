from datetime import datetime
from typing import Optional
from database import get_db_connection
from utils.helpers import generate_uuid, get_current_datetime, safe_datetime_for_db
from exceptions import NotFoundError, DatabaseError
from config import settings
import logging

# logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class UserService:
    """Service for user operations"""
    
    @staticmethod
    async def get_or_create_user(email: str = None) -> str:
        """Get or create a user by email"""
        email = email or settings.DEFAULT_USER_EMAIL
        
        try:
            async with get_db_connection() as conn:
                # Check if user exists
                user = await conn.fetchrow(
                    "SELECT id FROM users WHERE email = $1",
                    email
                )
                if user:
                    return str(user['id'])
                
                # Create a new user with proper datetime
                user_id = generate_uuid()
                now = get_current_datetime()
                
                # Ensure we have a datetime object
                safe_now: datetime = safe_datetime_for_db(now)

                logger.info(f"Creating new user with ID: {user_id} and date_type: {type(safe_now)}")

                await conn.execute(
                    "INSERT INTO users (id, email, created_at, updated_at) VALUES ($1, $2, $3, $4)",
                    user_id, email, safe_now, safe_now
                )
                print(f"Created new user with ID: {user_id}")
                return user_id
        except Exception as e:
            raise DatabaseError(f"Failed to get or create user: {str(e)}")
    
    @staticmethod
    async def get_user(user_id: str) -> Optional[dict]:
        """Get user by ID"""
        try:
            async with get_db_connection() as conn:
                user = await conn.fetchrow(
                    "SELECT id, email, created_at FROM users WHERE id = $1",
                    user_id
                )
                if user:
                    return {
                        "id": str(user['id']),
                        "email": user['email'],
                        "created_at": user['created_at'].isoformat() if user['created_at'] else None
                    }
                return None
        except Exception as e:
            raise DatabaseError(f"Failed to get user: {str(e)}")
    
    @staticmethod
    async def get_current_user() -> dict:
        """Get current user (default user)"""
        user_id = await UserService.get_or_create_user()
        user = await UserService.get_user(user_id)
        if not user:
            raise NotFoundError("User not found")
        return user