import re
from typing import List, Dict, Any
from utils.validators import is_valid_scraping_url

class InputDetector:
    """Detect input type (URL, question, etc.)"""
    
    URL_PATTERN = re.compile(
        r'^https?://[^\s]+', re.IGNORECASE
    )
    
    QUESTION_PATTERN = re.compile(
        r'^(what|where|when|why|how|who|which|is|are|do|does|did|can|could|would|will|should|'
        r'explain|describe|tell|show|give|find|search|analyze|summarize|extract|compare|'
        r'contrast|evaluate|discuss|clarify|define|list|provide|identify)\b',
        re.IGNORECASE
    )
    
    @classmethod
    def detect_input_type(cls, content: str) -> Dict[str, Any]:
        """Detect the type of input content"""
        content = content.strip()
        
        # Check for URLs
        raw_urls = re.findall(r'https?://[^\s]+', content)
        
        # Filter - only HTTPS
        valid_urls = [url for url in raw_urls if is_valid_scraping_url(url)]
        has_valid_url = len(valid_urls) > 0
        
        # Check if it's a question
        has_question = bool(cls.QUESTION_PATTERN.search(content)) or content.endswith('?')
        
        # Determine input type
        if has_valid_url:
            input_type = 'url'
        elif has_question:
            input_type = 'question'
        else:
            input_type = 'message'
        
        return {
            'has_url': has_valid_url,
            'urls': valid_urls,
            'has_question': has_question,
            'input_type': input_type
        }
    
    @classmethod
    def extract_urls(cls, content: str) -> List[str]:
        """Extract all URLs from content"""
        # Simple URL extraction - matches http:// and https://
        # More sophisticated version would handle URLs in text
        urls = re.findall(r'https?://[^\s]+', content)
        return urls