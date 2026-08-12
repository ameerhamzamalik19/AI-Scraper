from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Any
from datetime import datetime


class LinkRequest(BaseModel):
    """Request model for processing links/messages"""
    content: str = Field(..., description="User input content (URL or message)", min_length=1)
    chat_id: Optional[str] = Field(None, description="Chat session ID (conversation ID)")
    user_id: Optional[str] = Field(None, description="User ID (UUID)")
    
    @field_validator('content')
    @classmethod
    def validate_content(cls, v: str) -> str:
        """Validate that content is not empty"""
        v = v.strip()
        if not v:
            raise ValueError('Content cannot be empty')
        return v


class MessageResponse(BaseModel):
    """Message response model"""
    role: str
    content: str
    timestamp: str


class ChatResponse(BaseModel):
    """Chat response model"""
    id: str
    title: str
    created_at: Optional[str]
    updated_at: Optional[str]
    messages: Optional[List[MessageResponse]] = None


class UserResponse(BaseModel):
    """User response model"""
    user_id: str
    email: str
    created_at: Optional[str]


class ProcessLinkResponse(BaseModel):
    """Response model for process-link endpoint"""
    chat_id: str
    user_id: str
    project_id: str
    message: MessageResponse
    detection: dict
    scraping_job_id: Optional[str]


class ScrapingJobResponse(BaseModel):
    """Scraping job response model"""
    id: str
    url: str
    status: str
    error: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]


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
    """Message response model"""
    id: str
    chat_id: str
    user_id: str
    role: str
    content: str
    is_url: bool
    url_processed: bool
    created_at: str
    updated_at: str


class ChatWithMessagesResponse(BaseModel):
    """Chat with messages response"""
    id: str
    title: str
    user_id: str
    project_id: str
    created_at: str
    updated_at: str
    messages: List[MessageResponse] = []