from typing import Optional
from urllib.parse import urlparse, urlunparse
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



def normalize_url(url: str) -> str:
        """Normalize a URL by removing trailing slashes, fragments, etc."""
        parsed = urlparse(url)
        # Remove fragment
        normalized = parsed._replace(fragment='')
        # Remove trailing slash from path
        if normalized.path.endswith('/'):
            normalized = normalized._replace(path=normalized.path[:-1])
        return urlunparse(normalized)

def is_valid_scraping_url(url: str) -> bool:
    """Validate if a URL is valid for scraping - HTTPS only"""
    try:
        parsed = urlparse(url)
        
        # Must have a scheme and it must be HTTPS
        if not parsed.scheme or parsed.scheme != 'https':
            return False
        
        # Must have a domain
        if not parsed.netloc:
            return False
        
        # Must have a dot (domain)
        if '.' not in parsed.netloc:
            return False
        
        return True
        
    except Exception:
        return False


def is_valid_url_for_scraping(url: str) -> tuple[bool, str]:
    """Validate URL for scraping and return error message if invalid"""
    if not url or not url.strip():
        return False, "URL cannot be empty"
    
    url = url.strip()
    
    if not url.startswith('https://'):
        if url.startswith('http://'):
            return False, "HTTP is not allowed. Please use HTTPS."
        return False, "URL must start with https://"
    
    parsed = urlparse(url)
    
    if not parsed.netloc:
        return False, "Invalid URL format"
    
    if '.' not in parsed.netloc:
        return False, "Invalid domain format"
    
    return True, ""