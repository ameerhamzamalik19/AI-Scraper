from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Any
from datetime import datetime


class LinkRequest(BaseModel):
    """Request model for processing links/messages"""
    content: str = Field(..., description="User input content (URL or message)", min_length=1)
    chat_id: Optional[str] = Field(None, description="Chat session ID (conversation ID)")
    user_id: Optional[str] = Field(None, description="User ID (UUID)")
    project_id: Optional[str] = Field(None, description="Project ID")

    @field_validator('content')
    @classmethod
    def validate_content(cls, v: str) -> str:
        """Validate that content is not empty"""
        v = v.strip()
        if not v:
            raise ValueError('Content cannot be empty')
        return v


class MessageCreate(BaseModel):
    """Create a new message"""
    chat_id: str
    user_id: str
    role: str  # 'user' or 'assistant'
    content: str
    is_url: bool = False
    
    @field_validator('role')
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in ['user', 'assistant']:
            raise ValueError('Role must be "user" or "assistant"')
        return v


class MessageResponse(BaseModel):
    """Message response model with all fields"""
    id: str
    chat_id: str
    user_id: str
    role: str
    content: str
    is_url: bool = False
    url_processed: bool = False
    timestamp: str  # ISO timestamp (maps to created_at)
    created_at: str
    updated_at: str


class DetectionResult(BaseModel):
    """Detection result for input content"""
    has_url: bool
    urls: List[str]
    has_question: bool
    input_type: str


class ProcessLinkResponse(BaseModel):
    """Response model for process-link endpoint"""
    chat_id: str
    user_id: str
    project_id: str
    message: MessageResponse
    detection: DetectionResult
    scraping_job_id: Optional[str] = None
    is_new_chat: bool = False


class ChatResponse(BaseModel):
    """Chat response with messages"""
    id: str
    user_id: str
    project_id: Optional[str] = None
    title: Optional[str] = None
    message_count: int = 0
    last_message_at: Optional[str] = None
    messages: Optional[List[MessageResponse]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ChatWithMessagesResponse(BaseModel):
    """Chat with messages response (legacy compatibility)"""
    id: str
    title: str
    user_id: str
    project_id: str
    created_at: str
    updated_at: str
    messages: List[MessageResponse] = []


class ScrapingJobResponse(BaseModel):
    """Scraping job response model"""
    id: str
    url: str
    status: str
    error: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class UserResponse(BaseModel):
    """User response model"""
    id: str
    email: str
    created_at: Optional[str] = None