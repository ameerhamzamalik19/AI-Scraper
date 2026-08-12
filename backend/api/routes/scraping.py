from typing import Optional, List
from fastapi import APIRouter, Depends
from services.scraping_service import ScrapingService
from api.deps import get_user_id_from_header
from models import ScrapingJobResponse

router = APIRouter(prefix="/api/scraping-jobs", tags=["scraping"])


@router.get("/{chat_id}", response_model=List[ScrapingJobResponse])
async def get_scraping_jobs(
    chat_id: str,
    user_id: Optional[str] = Depends(get_user_id_from_header)
):
    """Get scraping jobs for a chat"""
    return await ScrapingService.get_scraping_jobs(chat_id, user_id)