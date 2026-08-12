import re
from urllib.parse import urlparse
from typing import List, Dict


class InputDetector:
    """Accurate input detection for URLs vs messages"""
    
    URL_PATTERN = re.compile(
        r'(?:https?://|www\.)'  # Protocol or www
        r'(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}'  # Domain
        r'(?:/[^\s]*)?',  # Path
        re.IGNORECASE
    )
    
    @classmethod
    def extract_urls(cls, text: str) -> List[str]:
        """Extract all URLs from text"""
        return cls.URL_PATTERN.findall(text)
    
    @staticmethod
    def is_valid_url(url: str) -> bool:
        """Check if a string is a valid URL"""
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        
        try:
            result = urlparse(url)
            if not all([result.scheme, result.netloc]):
                return False
            if result.scheme not in ['http', 'https']:
                return False
            if '.' not in result.netloc:
                return False
            return True
        except Exception:
            return False
    
    @classmethod
    def detect_input_type(cls, content: str) -> Dict:
        """Detect the type of input"""
        content = content.strip()
        extracted_urls = cls.extract_urls(content)
        
        valid_urls = []
        for url in extracted_urls:
            if cls.is_valid_url(url):
                if not url.startswith(('http://', 'https://')):
                    url = 'https://' + url
                valid_urls.append(url)
        
        text_content = content
        for url in extracted_urls:
            text_content = text_content.replace(url, '').strip()
        
        input_type = 'message'
        if valid_urls and not text_content:
            input_type = 'url'
        elif valid_urls and text_content:
            input_type = 'mixed'
        
        return {
            'type': input_type,
            'urls': valid_urls,
            'text_content': text_content,
            'has_url': len(valid_urls) > 0
        }