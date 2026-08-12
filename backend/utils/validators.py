from typing import Optional
from pydantic import ValidationError


class RequestValidator:
    """Request validation utilities"""
    
    @staticmethod
    def validate_uuid(value: Optional[str], field_name: str = "ID") -> Optional[str]:
        """Validate UUID format"""
        if value is None:
            return None
        import uuid
        try:
            uuid.UUID(value)
            return value
        except ValueError:
            raise ValueError(f"Invalid {field_name} format: {value}")
    
    @staticmethod
    def validate_url(url: str) -> str:
        """Validate URL format"""
        from urllib.parse import urlparse
        try:
            result = urlparse(url)
            if not all([result.scheme, result.netloc]):
                raise ValueError(f"Invalid URL: {url}")
            return url
        except Exception:
            raise ValueError(f"Invalid URL: {url}")