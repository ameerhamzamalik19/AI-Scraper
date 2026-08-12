import uuid
from datetime import datetime, timezone
from typing import Optional, Union

def generate_uuid() -> str:
    """Generate a UUID"""
    return str(uuid.uuid4())


def get_current_datetime() -> datetime:
    """Get current UTC datetime"""
    return datetime.now(timezone.utc)


def get_iso_timestamp() -> str:
    """Get current timestamp as ISO string"""
    return get_current_datetime().isoformat()


def truncate_text(text: str, max_length: int = 40, suffix: str = "...") -> str:
    """Truncate text to max length"""
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    return text[:max_length] + suffix


def extract_domain(url: str) -> str:
    """Extract domain from URL"""
    from urllib.parse import urlparse
    try:
        domain = urlparse(url).netloc
        return domain.replace('www.', '')
    except Exception:
        return url[:30]

def parse_datetime(value: Union[str, datetime, None]) -> Optional[datetime]:
    """
    Parse a datetime value with proper error handling.
    Converts string to datetime if needed, returns datetime as-is.
    """
    if value is None:
        return None
    
    # If it's already a datetime, return it
    if isinstance(value, datetime):
        return value
    
    # If it's a string, try to parse it
    if isinstance(value, str):
        try:
            # Try ISO format first
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            try:
                # Try common formats
                for fmt in [
                    "%Y-%m-%d %H:%M:%S.%f%z",
                    "%Y-%m-%d %H:%M:%S%z",
                    "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%dT%H:%M:%S.%f%z",
                    "%Y-%m-%dT%H:%M:%S%z",
                ]:
                    try:
                        return datetime.strptime(value, fmt)
                    except ValueError:
                        continue
            except Exception:
                pass
            
            # If all parsing fails, raise error
            raise ValueError(f"Invalid datetime format: {value}")
    
    raise ValueError(f"Unsupported type for datetime: {type(value)}")


def safe_datetime_for_db(value: Union[str, datetime, None]) -> Optional[datetime]:
    """
    Safely convert a value to datetime for database insertion.
    Returns None for None values, raises error for invalid values.
    """
    if value is None:
        return None
    
    try:
        return parse_datetime(value)
    except Exception as e:
        # Log the error and re-raise with context
        print(f"Error converting to datetime: {e}, value: {value}")
        raise ValueError(f"Failed to convert to datetime: {e}")


def truncate_text(text: str, max_length: int = 40, suffix: str = "...") -> str:
    """Truncate text to max length"""
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    return text[:max_length] + suffix


def extract_domain(url: str) -> str:
    """Extract domain from URL"""
    from urllib.parse import urlparse
    try:
        domain = urlparse(url).netloc
        return domain.replace('www.', '')
    except Exception:
        return url[:30]


def generate_response(content: str, detection: dict) -> str:
    """Generate an appropriate response based on input type"""
    response_parts = []
    
    if detection['type'] == 'url':
        url = detection['urls'][0]
        response_parts.append(f"🔗 **URL detected and queued for scraping:**\n")
        response_parts.append(f"URL: {url}")
        response_parts.append(f"\n📋 The URL has been added to the scraping queue.")
        response_parts.append("You'll receive the scraped content once processing is complete.")
        response_parts.append("\n💡 In the meantime, feel free to ask questions about this URL.")
        
    elif detection['type'] == 'mixed':
        url = detection['urls'][0]
        text = detection['text_content']
        response_parts.append(f"💬 **Message with URL detected:**\n")
        response_parts.append(f"Message: {text}")
        response_parts.append(f"URL: {url}")
        response_parts.append(f"\n📋 The URL has been added to the scraping queue.")
        response_parts.append("I'll process both your message and the URL content together.")
        response_parts.append("\n💡 You can continue the conversation while the URL is being processed.")
        
    else:  # message
        response_parts.append(f"💬 **Message received:**\n")
        response_parts.append(f"Your message: {content}")
        response_parts.append(f"\n📝 Message saved to database.")
        response_parts.append("\n💡 To get more detailed responses, you can:")
        response_parts.append("• Include a URL for context")
        response_parts.append("• Ask a specific question")
        response_parts.append("• Provide more details about your request")
    
    return "\n".join(response_parts)