from fastapi import APIRouter
from services.user_service import UserService
from models import UserResponse

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/current", response_model=UserResponse)
async def get_current_user():
    """Get current user information"""
    return await UserService.get_current_user()