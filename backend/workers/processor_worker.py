# workers/processor_worker.py
import dramatiq
import logging
import json
import re
import base64
import mimetypes
import os
from urllib.parse import urljoin, urlparse
from typing import Dict, Any, List, Optional, Tuple
import requests
from ollama import Client as OllamaClient
from database_sync import execute_query, execute_update, execute_one
from utils.helpers import get_current_datetime
from redis_config import PROCESSING_QUEUE_NAME
from crawler.content_processor import ContentProcessor
from utils.chat_status_tracker import ChatStatusTracker
from utils.progress_tracker import get_progress_tracker
from utils.content_classifier import ContentType, ContentClassifier
from config import crawler_settings  # ADDED: Import crawler_settings
from utils.queue_dispatch import enqueue_worker
from utils.json_utils import safe_json_loads

logger = logging.getLogger(__name__)
from utils.worker_event_loop import start_worker_event_loop

start_worker_event_loop("processor")


try:
    from bs4 import BeautifulSoup
    import markdownify
    from PIL import Image
    import io
    print("✅ Processor dependencies imported successfully")
except Exception as e:
    print(f"❌ Failed to import processor dependencies: {e}")
    raise


class OllamaVisionClient:
    """Client for Ollama cloud free tier using the official ollama Python library."""

    def __init__(self, model: str = None):
        self.model = model or os.getenv('OLLAMA_VISION_MODEL', 'gemma4:31b-cloud')
        api_key = os.getenv('OLLAMA_API_KEY', '')
        self._client = OllamaClient(
            host="https://ollama.com",
            headers={'Authorization': f'Bearer {api_key}'} if api_key else {}
        )

    def _chat(self, messages: List[Dict[str, Any]], num_predict: int = 512, temperature: float = 0.2,
              response_format: Optional[str] = None) -> Optional[str]:
        """
        Internal method — send a chat request and return the content string.
        All public methods funnel through here for consistent error handling.
        """
        try:
            chat_options = {
                "temperature": temperature,
                "top_p": 0.9,
                "num_predict": num_predict,
            }
            request = {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "options": chat_options,
            }
            if response_format:
                request["format"] = response_format
            response = self._client.chat(
                **request
            )
            return response['message']['content'].strip()
        except Exception as e:
            logger.error(f"Ollama API call failed: {e}")
            return None

    def generate_description(
        self,
        prompt: str,
        image_data: Optional[str] = None,
        text_context: Optional[str] = None
    ) -> Optional[str]:
        """
        Generate a description using gemma4:31b-cloud.
        Supports both text-only and vision (image) calls.
        """
        full_prompt = prompt
        if text_context:
            full_prompt = f"{prompt}\n\nContext from page: {text_context}"

        if image_data:
            # Vision call — image goes in the images field of the user message
            messages = [
                {
                    "role": "user",
                    "content": full_prompt,
                    "images": [image_data]
                }
            ]
        else:
            messages = [
                {"role": "user", "content": full_prompt}
            ]

        return self._chat(messages, num_predict=512, temperature=0.2)

    def analyze_image_with_vision(self, image_data: str, prompt: str) -> Optional[str]:
        """
        Dedicated vision call with lower temperature for factual extraction.
        """
        messages = [
            {
                "role": "user",
                "content": prompt,
                "images": [image_data]
            }
        ]
        return self._chat(messages, num_predict=1024, temperature=0.1)

    def extract_structured_data(self, image_data: str, data_type: str = "general") -> Optional[Dict]:
        """
        Extract structured data from an image using vision.
        Returns parsed JSON dict or a plain-text fallback dict.
        """
        prompts = {
            "general": """Analyze this image and extract ALL structured information.

Return ONLY valid JSON with this schema:
{
  "content_type": "team_photo|chart|infographic|screenshot|diagram|table|product_photo|document|other",
  "visible_text": "ALL text visible in the image",
  "entities": [
    {
      "type": "person|organization|product|service|date|number|metric|label|location|event|other",
      "name": "Entity name or label",
      "value": "Associated value if applicable",
      "context": "Additional context from the image"
    }
  ],
  "relationships": [
    {
      "source": "Entity A",
      "relation": "has_role|works_for|leads|contains|costs|represents|other",
      "target": "Entity B"
    }
  ],
  "headings": ["Main headings or titles visible"],
  "summary": "Brief factual summary (2-3 sentences)",
  "searchable_text": "Concatenated text optimized for search retrieval"
}

Extract EVERY piece of information visible. Be comprehensive and factual.""",

            "table": """Analyze this table image and extract ALL data.
Return ONLY valid JSON:
{
  "content_type": "table",
  "headers": ["column1", "column2"],
  "rows": [["value1", "value2"]],
  "caption": "Table caption if visible",
  "summary": "Brief description of what this table shows",
  "searchable_text": "Combined text for search"
}""",

            "chart": """Analyze this chart/graph and extract key information.
Return ONLY valid JSON:
{
  "content_type": "chart",
  "chart_type": "bar|line|pie|scatter|area|other",
  "title": "Chart title if visible",
  "x_axis": "X-axis label",
  "y_axis": "Y-axis label",
  "data_points": [{"label": "point1", "value": "value1"}],
  "summary": "What this chart shows",
  "searchable_text": "Combined text for search"
}"""
        }

        prompt = prompts.get(data_type, prompts["general"])
        response = self._chat(
            [{"role": "user", "content": prompt, "images": [image_data]}],
            num_predict=1024,
            temperature=0.1,
            response_format="json",
        )

        if not response:
            return None

        parsed = safe_json_loads(response, context="ollama_vision")
        if parsed is not None:
            return parsed

        return {
            "content_type": "image",
            "visible_text": response,
            "searchable_text": response
        }


class MediaAnalyzer:
    """Analyze media assets with Ollama, using heuristics to skip unnecessary calls"""
    
    def __init__(self):
        self.ollama = OllamaVisionClient()
        self.ollama_enabled = bool(os.getenv('OLLAMA_API_KEY', '').strip())
        
    def should_analyze_image(self, img_element, source_url: str, width: int = 0, height: int = 0) -> Tuple[bool, str]:
        """
        Determine if an image should be analyzed by Ollama.
        Returns: (should_analyze, reason)
        """
        # Skip if Ollama is disabled
        if not self.ollama_enabled:
            return False, "Ollama disabled"
        
        # Check size - skip icons and decorators
        if width > 0 and height > 0:
            if width < 100 or height < 100:
                return False, f"Image too small ({width}x{height}) - likely icon/decorator"
        
        # Check if image is likely an icon based on src/class
        src = source_url.lower()
        alt_text = img_element.get('alt', '').strip().lower()
        class_attr = img_element.get('class', [])
        if isinstance(class_attr, str):
            class_attr = class_attr.split()
        
        icon_indicators = ['icon', 'logo', 'button', 'arrow', 'bullet', 'dot', 'separator', 'line', 'spacer', 'bg-', 'background']
        for indicator in icon_indicators:
            if indicator in src or any(indicator in cls.lower() for cls in class_attr):
                return False, f"Appears to be icon/decorator (indicator: {indicator})"
        
        # Skip if no alt text and no parent text context
        if not alt_text:
            # Check if parent has text content
            parent = img_element.parent
            has_context = False
            for _ in range(3):  # Check up to 3 levels up
                if parent and parent.get_text(strip=True):
                    has_context = True
                    break
                parent = parent.parent if parent else None
            
            if not has_context:
                return False, "No alt text and no surrounding text context - likely decorative"
        
        # Check if image is referenced from a likely chart/data source
        chart_indicators = ['chart', 'graph', 'plot', 'diagram', 'infographic', 'screenshot', 'screen-shot']
        is_chart = any(indicator in src.lower() or indicator in alt_text for indicator in chart_indicators)
        
        if is_chart:
            return True, "Likely chart/graph/screenshot with semantic value"
        
        # For images with alt text but not clearly charts, analyze if they have significant size
        if width > 200 or height > 200:
            return True, "Image with meaningful dimensions and context"
        
        # Default: don't analyze to save quota
        return False, "Low-confidence image, skipping to save quota"
    
    def should_analyze_table(self, table_element) -> Tuple[bool, str]:
        """Determine if a table should be analyzed by Ollama."""
        if not self.ollama_enabled:
            return False, "Ollama disabled"
        
        try:
            rows = table_element.find_all('tr')
            if len(rows) < 2:
                return False, f"Table has only {len(rows)} rows (min 2 needed)"
            
            # Check columns
            max_cols = 0
            for row in rows:
                cols = len(row.find_all(['td', 'th']))
                max_cols = max(max_cols, cols)
            
            if max_cols < 2:
                return False, f"Table has only {max_cols} columns (min 2 needed)"
            
            # Check if table parses cleanly to markdown
            markdown = DocumentProcessor._extract_table_markdown(table_element)
            if markdown and self._is_clean_markdown_table(markdown):
                # Parse as markdown - no LLM needed
                return False, "Table parses cleanly to markdown format"
            
            # Complex tables (nested, merged cells, etc.)
            return True, "Complex table with structural formatting"
            
        except Exception as e:
            logger.warning(f"Error analyzing table: {e}")
            return False, "Error analyzing table, skipping"
    
    def _is_clean_markdown_table(self, markdown: str) -> bool:
        """Check if a table is cleanly parsed to markdown."""
        lines = markdown.strip().split('\n')
        if len(lines) < 3:  # Need header, separator, at least 1 row
            return False
        # Check if separator line exists
        return any('---' in line for line in lines)
    
    def generate_image_description(self, img_element, image_data: str, source_url: str) -> Optional[str]:
        """Generate a searchable description for an image using Ollama."""
        # Get context
        alt_text = img_element.get('alt', '').strip()
        title = img_element.get('title', '').strip()
        
        # Gather surrounding text context
        context = []
        parent = img_element.parent
        for _ in range(3):  # Up to 3 levels up
            if parent:
                # Get text from siblings and parent
                text = parent.get_text(strip=True)
                if text:
                    # Get text around the image (before/after within parent)
                    context.append(text)
                parent = parent.parent if parent else None
            else:
                break
        
        # Try to get caption/figcaption
        caption = None
        figcaption = img_element.find_parent('figure')
        if figcaption:
            cap = figcaption.find('figcaption')
            if cap:
                caption = cap.get_text(strip=True)
        
        context_text = " ".join(filter(None, [alt_text, title, caption] + context))
        context_text = context_text[:500]  # Limit context length
        
        prompt = f"""You are a precise data extractor. Analyze this image and describe ONLY the factual information that would help someone find it in a search query.

Focus on:
- Main subject/what's shown (e.g., "growth chart", "team photo", "architecture diagram")
- Any visible text, labels, numbers, or data points
- Key visual elements that matter
- The type of content (chart, screenshot, diagram, photo, infographic, etc.)

DO NOT add opinions, speculation, or marketing language.
Provide a concise, fact-based description optimized for semantic search.

{context_text if context_text else "No additional context available."}"""

        try:
            description = self.ollama.generate_description(prompt, image_data)
            if description:
                # Clean up the description
                description = description.strip()
                # Remove any markdown formatting that might have been added
                description = re.sub(r'^["\']|["\']$', '', description)
                return description
            return None
        except Exception as e:
            logger.error(f"Failed to generate image description: {e}")
            return None
    
    def generate_table_description(self, table_element, table_text: str) -> Optional[str]:
        """Generate a searchable description for a complex table using Ollama."""
        # Extract table structure
        try:
            headers = []
            rows_data = []
            
            # Get headers
            thead = table_element.find('thead')
            if thead:
                for th in thead.find_all(['th', 'td']):
                    headers.append(th.get_text(strip=True))
            
            # If no thead, try first row as headers
            if not headers:
                first_row = table_element.find('tr')
                if first_row:
                    for th in first_row.find_all(['th', 'td']):
                        headers.append(th.get_text(strip=True))
            
            # Get data rows
            for tr in table_element.find_all('tr'):
                cells = []
                for td in tr.find_all(['td', 'th']):
                    cells.append(td.get_text(strip=True))
                if cells:
                    rows_data.append(cells)
            
            # Prepare structured table data
            table_summary = f"Headers: {headers}\n\nRows: {rows_data[:10]}"  # First 10 rows max
            
            prompt = f"""Analyze this table data and create a searchable description.

Table data:
{table_summary}

Provide a concise description that would help someone find this table in a search. Include:
- The type of data presented
- Key relationships between columns
- Important numbers or patterns
- What this table is about

DO NOT add speculation or marketing. Just factual description."""

            description = self.ollama.generate_description(prompt)
            if description:
                return description.strip()
            return None
            
        except Exception as e:
            logger.error(f"Failed to generate table description: {e}")
            return None
    
    def extract_structured_content(self, image_data: str, source_url: str, content_type: str = "general") -> Optional[Dict]:
        """
        Generic structured content extraction from images.
        This extracts ANY structured information, not just specific types.
        """
        try:
            result = self.ollama.extract_structured_data(image_data, content_type)
            if result:
                logger.info(f"📊 Extracted structured content: {result.get('content_type', 'unknown')}")
                return result
            return None
        except Exception as e:
            logger.error(f"Structured content extraction failed: {e}")
            return None


class DocumentProcessor:
    """Clean and process raw HTML into Markdown with full content preservation"""

    _media_http = requests.Session()
    
    @staticmethod
    def clean_html(html: str) -> str:
        """
        Clean HTML by removing boilerplate while preserving main content.
        This is the FIRST step - removes navigation, headers, footers, etc.
        """
        soup = BeautifulSoup(html, 'html.parser')
        
        # Remove non-content tags
        for tag in soup(['script', 'style', 'noscript', 'iframe', 'svg']):
            tag.decompose()
        
        # Remove boilerplate elements (navigation, footer, sidebar, etc.)
        boilerplate_selectors = [
            'nav', '.nav', '.navigation', '.menu',
            '.sidebar', '.side-bar', '.widget',
            '.ad', '.advertisement', '.ad-container',
            '.social', '.share', '.share-buttons',
            '.footer', '.footer-content',
            '.header', '.header-content',
            '.cookie', '.cookie-notice', '.cookie-banner',
            '.newsletter', '.subscribe', '.signup',
            '.popup', '.modal', '.overlay',
            '.comments', '.comment-section',
            '.related-articles', '.recommended',
            '.breadcrumb', '.breadcrumbs',
            '.pagination', '.page-numbers',
            '.search', '.search-form',
            '.tags', '.categories',
        ]
        
        for selector in boilerplate_selectors:
            for element in soup.select(selector):
                element.decompose()
        
        return str(soup)
    
    @staticmethod
    def html_to_markdown(html: str) -> str:
        """
        Convert HTML to Markdown preserving ALL content.
        This uses the new enhanced extraction while maintaining backward compatibility.
        """
        try:
            # First clean the HTML (remove boilerplate)
            cleaned_html = DocumentProcessor.clean_html(html)
            
            # Use BeautifulSoup to parse and preserve structure
            soup = BeautifulSoup(cleaned_html, 'html.parser')
            
            # Build markdown from structured content
            markdown_parts = []
            
            # Process body content
            body = soup.find('body')
            if not body:
                body = soup
            
            # Extract content preserving structure
            markdown_parts.extend(DocumentProcessor._extract_element_content(body))
            
            # Join and clean up
            markdown = '\n\n'.join(filter(None, markdown_parts))
            markdown = re.sub(r'\n{3,}', '\n\n', markdown)
            markdown = markdown.strip()
            
            return markdown
            
        except Exception as e:
            logger.error(f"Error converting HTML to markdown: {e}")
            # Fallback: use markdownify library
            try:
                cleaned_html = DocumentProcessor.clean_html(html)
                md = markdownify.markdownify(
                    cleaned_html,
                    heading_style="ATX",
                    bullets="-",
                    strip=['script', 'style', 'noscript', 'iframe'],
                    convert_internally=True
                )
                md = re.sub(r'\n\s*\n', '\n\n', md)
                return md.strip()
            except Exception as e2:
                logger.error(f"Fallback markdown conversion also failed: {e2}")
                return html
    
    @staticmethod
    def _extract_element_content(element, level: int = 0) -> List[str]:
        """
        Recursively extract content from HTML elements preserving structure.
        This is the key method that extracts ALL content, not just headings.
        """
        parts = []
        indent = "  " * level
        
        if element.name is None:
            # Text node
            text = element.string
            if text and text.strip():
                parts.append(text.strip())
            return parts
        
        # Handle different element types
        tag = element.name.lower()
        
        # Headings - preserve hierarchy
        if tag in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            text = element.get_text().strip()
            if text:
                level_num = int(tag[1])
                prefix = '#' * level_num
                parts.append(f"{prefix} {text}")
        
        # Paragraphs
        elif tag == 'p':
            text = element.get_text().strip()
            if text and len(text) > 10:  # Skip very short paragraphs
                parts.append(text)
        
        # Lists
        elif tag in ['ul', 'ol']:
            list_items = []
            for li in element.find_all('li', recursive=False):
                li_text = li.get_text().strip()
                if li_text:
                    # Check if list item has sub-content
                    sub_items = []
                    for child in li.find_all(['ul', 'ol'], recursive=True):
                        sub_items.extend(DocumentProcessor._extract_element_content(child, level + 1))
                    
                    if sub_items:
                        list_items.append(f"- {li_text}")
                        list_items.extend([f"  {item}" for item in sub_items])
                    else:
                        list_items.append(f"- {li_text}")
            
            if list_items:
                parts.append('\n'.join(list_items))
        
        # Tables - preserve structure
        elif tag == 'table':
            table_text = DocumentProcessor._extract_table_markdown(element)
            if table_text:
                parts.append(table_text)
        
        # Divs and sections - extract their content
        elif tag in ['div', 'section', 'article', 'main', 'aside']:
            # Only process if it has meaningful content
            text = element.get_text().strip()
            if len(text) > 20:
                # Process children
                for child in element.children:
                    child_parts = DocumentProcessor._extract_element_content(child, level + 1)
                    if child_parts:
                        parts.extend(child_parts)
        
        # Other block elements
        elif tag in ['blockquote', 'pre', 'code']:
            text = element.get_text().strip()
            if text:
                if tag == 'blockquote':
                    parts.append(f"> {text}")
                else:
                    parts.append(text)
        
        # Process children for other elements (like spans, strong, em)
        else:
            for child in element.children:
                child_parts = DocumentProcessor._extract_element_content(child, level + 1)
                if child_parts:
                    parts.extend(child_parts)
        
        return parts
    
    @staticmethod
    def _extract_table_markdown(table_element) -> Optional[str]:
        """Extract table as markdown preserving structure."""
        try:
            rows = []
            
            # Extract headers
            headers = []
            thead = table_element.find('thead')
            if thead:
                for th in thead.find_all(['th', 'td']):
                    headers.append(th.get_text().strip())
            else:
                # Try first row as headers
                first_row = table_element.find('tr')
                if first_row:
                    for th in first_row.find_all(['th', 'td']):
                        headers.append(th.get_text().strip())
            
            if headers:
                rows.append("| " + " | ".join(headers) + " |")
                rows.append("|" + "|".join(["---"] * len(headers)) + "|")
            
            # Extract data rows
            for tr in table_element.find_all('tr'):
                # Skip if this is the header row
                if tr == table_element.find('tr') and not thead:
                    continue
                
                cells = []
                for td in tr.find_all(['td', 'th']):
                    cells.append(td.get_text().strip())
                
                if cells:
                    # Pad to match header count
                    while len(cells) < len(headers):
                        cells.append("")
                    rows.append("| " + " | ".join(cells) + " |")
            
            if len(rows) <= 1:  # Only headers or empty
                return None
            
            return "**Table:**\n\n" + "\n".join(rows)
            
        except Exception as e:
            logger.warning(f"Error extracting table: {e}")
            return None
    
    @staticmethod
    def extract_metadata_from_html(html: str) -> Dict[str, Any]:
        """Extract additional metadata from HTML"""
        soup = BeautifulSoup(html, 'html.parser')
        
        metadata = {
            'title': None,
            'description': None,
            'keywords': None,
            'canonical_url': None,
            'language': None,
            'og_title': None,
            'og_description': None,
            'og_image': None,
        }
        
        title = soup.find('title')
        if title:
            metadata['title'] = title.get_text().strip()
        
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc:
            metadata['description'] = meta_desc.get('content', '').strip()
        
        meta_keywords = soup.find('meta', attrs={'name': 'keywords'})
        if meta_keywords:
            metadata['keywords'] = meta_keywords.get('content', '').strip()
        
        canonical = soup.find('link', attrs={'rel': 'canonical'})
        if canonical:
            metadata['canonical_url'] = canonical.get('href', '').strip()
        
        html_tag = soup.find('html')
        if html_tag:
            metadata['language'] = html_tag.get('lang', 'en')
        
        # Open Graph
        og_title = soup.find('meta', property='og:title')
        if og_title:
            metadata['og_title'] = og_title.get('content', '').strip()
        
        og_desc = soup.find('meta', property='og:description')
        if og_desc:
            metadata['og_description'] = og_desc.get('content', '').strip()
        
        og_image = soup.find('meta', property='og:image')
        if og_image:
            metadata['og_image'] = og_image.get('content', '').strip()
        
        return metadata

    @staticmethod
    def _image_data(source_url: str) -> tuple[str, str]:
        """Download an image and return its MIME type and base64 payload."""
        if source_url.startswith('data:'):
            header, payload = source_url.split(',', 1)
            mime_type = header[5:].split(';', 1)[0] or 'application/octet-stream'
            return mime_type, payload

        response = DocumentProcessor._media_http.get(source_url, timeout=30)
        response.raise_for_status()
        if len(response.content) > 10 * 1024 * 1024:
            raise ValueError('image exceeds 10 MB limit')
        mime_type = response.headers.get('content-type', '').split(';', 1)[0]
        mime_type = mime_type or mimetypes.guess_type(source_url)[0] or 'application/octet-stream'
        return mime_type, base64.b64encode(response.content).decode('ascii')

    @staticmethod
    def get_image_dimensions(source_url: str, image_data: Optional[str] = None) -> Tuple[int, int]:
        """Get image dimensions using PIL."""
        try:
            if image_data:
                # Decode base64
                img_bytes = base64.b64decode(image_data)
                img = Image.open(io.BytesIO(img_bytes))
                return img.width, img.height
            else:
                # Download and check
                response = DocumentProcessor._media_http.get(source_url, timeout=10)
                img = Image.open(io.BytesIO(response.content))
                return img.width, img.height
        except Exception as e:
            logger.debug(f"Could not get image dimensions: {e}")
            return 0, 0

    @staticmethod
    def describe_image(source_url: str, alt_text: str = '', title: str = '') -> str:
        """Create searchable image metadata without sending pixels to a text-only model."""
        filename = urlparse(source_url).path.rsplit('/', 1)[-1]
        details = [value.strip() for value in (alt_text, title, filename, source_url) if value and value.strip()]
        if not details:
            return 'Image asset captured from the crawled page.'
        return 'Image asset: ' + '. '.join(dict.fromkeys(details))

    @staticmethod
    def describe_table(table_text: str) -> str:
        """Generate a searchable description from table text."""
        # Simple fallback description without external API
        lines = table_text.strip().split('\n')[:10]
        summary = ' | '.join(line.strip() for line in lines if line.strip())
        if len(lines) > 10:
            summary += ' ... (truncated)'
        return f'Table data: {summary}'

    @staticmethod
    def extract_media(html: str, page_url: str) -> list[Dict[str, Any]]:
        """Extract images and tables with intelligent LLM-based descriptions."""
        soup = BeautifulSoup(html, 'html.parser')
        assets = []
        media_analyzer = MediaAnalyzer()
        
        # Process images - Use crawler_settings.MAX_IMAGES_TO_PROCESS
        max_images = getattr(crawler_settings, 'MAX_IMAGES_TO_PROCESS', 20)
        images = soup.find_all('img')[:max_images]
        
        # Log if we hit the limit
        total_images = len(soup.find_all('img'))
        if total_images > max_images:
            logger.info(f"Reached max images limit ({max_images}), skipping remaining {total_images - max_images} images")
        
        for image in images:
            source = image.get('src') or image.get('data-src')
            if not source:
                continue
            source_url = urljoin(page_url, source)

            source_lower = source_url.lower()
            alt_text = image.get('alt', '').strip().lower()
            class_attr = image.get('class', [])
            if isinstance(class_attr, str):
                class_attr = class_attr.split()
            icon_indicators = (
                'icon', 'logo', 'button', 'arrow', 'bullet', 'dot', 'separator',
                'line', 'spacer', 'bg-', 'background'
            )
            has_icon_indicator = any(
                indicator in source_lower or any(indicator in cls.lower() for cls in class_attr)
                for indicator in icon_indicators
            )
            if has_icon_indicator:
                assets.append({
                    'media_type': 'image',
                    'source_url': source_url,
                    'mime_type': mimetypes.guess_type(source_url)[0] or 'application/octet-stream',
                    'data_base64': None,
                    'description': DocumentProcessor.describe_image(source_url, image.get('alt', ''), image.get('title', '')),
                    'width': 0,
                    'height': 0,
                    'was_analyzed': False,
                    'structured_data': None,
                    'image_type': 'decorative',
                    'alt_text': image.get('alt', ''),
                    'caption': None,
                    'surrounding_text': None,
                    'section_heading': None,
                })
                continue

            if not alt_text:
                parent = image.parent
                has_context = False
                for _ in range(3):
                    if parent and parent.get_text(strip=True):
                        has_context = True
                        break
                    parent = parent.parent if parent else None
                if not has_context:
                    assets.append({
                        'media_type': 'image',
                        'source_url': source_url,
                        'mime_type': mimetypes.guess_type(source_url)[0] or 'application/octet-stream',
                        'data_base64': None,
                        'description': DocumentProcessor.describe_image(source_url, image.get('alt', ''), image.get('title', '')),
                        'width': 0,
                        'height': 0,
                        'was_analyzed': False,
                        'structured_data': None,
                        'image_type': 'decorative',
                        'alt_text': image.get('alt', ''),
                        'caption': None,
                        'surrounding_text': None,
                        'section_heading': None,
                    })
                    continue
            
            try:
                # Get image data
                mime_type, payload = DocumentProcessor._image_data(source_url)
                
                # Get dimensions
                width, height = DocumentProcessor.get_image_dimensions(source_url, payload)
                
                # Check if we should analyze this image
                should_analyze, reason = media_analyzer.should_analyze_image(
                    image, source_url, width, height
                )
                
                structured_data = None
                if should_analyze:
                    logger.info(f"🔍 Analyzing image: {source_url} - {reason}")

                    # One vision request provides both searchable text and
                    # structured facts; a second description request doubles
                    # latency for every analyzed image.
                    structured_data = media_analyzer.extract_structured_content(
                        payload, source_url, "general"
                    )

                    description = None
                    if structured_data:
                        description = (
                            structured_data.get('searchable_text') or
                            structured_data.get('summary') or
                            structured_data.get('visible_text')
                        )
                        if structured_data.get('visible_text') and (
                            structured_data['visible_text'] not in (description or '')
                        ):
                            description = f"{description or ''} | Visible text: {structured_data['visible_text']}"
                        if structured_data.get('entities'):
                            entity_text = " | ".join([
                                f"{e.get('name', '')} ({e.get('type', '')})" 
                                for e in structured_data.get('entities', [])[:5]
                            ])
                            if entity_text:
                                description = f"{description or ''} | Entities: {entity_text}"

                    if not description:
                        logger.warning(f"⚠️ Vision extraction failed, using fallback for {source_url}")
                        description = DocumentProcessor.describe_image(
                            source_url,
                            image.get('alt', ''),
                            image.get('title', '')
                        )
                else:
                    logger.debug(f"⏭️ Skipping image: {source_url} - {reason}")
                    description = DocumentProcessor.describe_image(
                        source_url,
                        image.get('alt', ''),
                        image.get('title', '')
                    )
                
                # Determine image type based on heuristics
                image_type = "decorative"
                if should_analyze:
                    if any(indicator in source_url.lower() for indicator in ['product', 'item', 'sku']):
                        image_type = "product"
                    elif any(indicator in source_url.lower() for indicator in ['chart', 'graph', 'plot', 'diagram']):
                        image_type = "diagram"
                    elif any(indicator in source_url.lower() for indicator in ['screenshot', 'screen-shot']):
                        image_type = "screenshot"
                    elif any(indicator in source_url.lower() for indicator in ['infographic', 'info-graphic']):
                        image_type = "infographic"
                    else:
                        image_type = "content"
                
                assets.append({
                    'media_type': 'image',
                    'source_url': source_url,
                    'mime_type': mime_type,
                    'data_base64': payload,
                    'description': description,
                    'width': width,
                    'height': height,
                    'was_analyzed': should_analyze,
                    'structured_data': structured_data,
                    'image_type': image_type,
                    'alt_text': image.get('alt', ''),
                    'caption': None,  # Will be filled from figcaption if found
                    'surrounding_text': None,  # Will be filled from context
                    'section_heading': None,  # Will be filled from parent heading
                })
                
                # Try to find caption
                figcaption = image.find_parent('figure')
                if figcaption:
                    cap = figcaption.find('figcaption')
                    if cap:
                        assets[-1]['caption'] = cap.get_text(strip=True)
                
                # Try to find section heading
                parent = image.parent
                for _ in range(5):
                    if parent:
                        heading = parent.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
                        if heading:
                            assets[-1]['section_heading'] = heading.get_text(strip=True)
                            break
                        parent = parent.parent
                    else:
                        break
                
            except Exception as e:
                logger.warning('Unable to process image %s: %s', source_url, e)

        # Process tables - Use crawler_settings.MAX_TABLES_TO_PROCESS
        max_tables = getattr(crawler_settings, 'MAX_TABLES_TO_PROCESS', 20)
        tables = soup.find_all('table')[:max_tables]
        
        # Log if we hit the limit
        total_tables = len(soup.find_all('table'))
        if total_tables > max_tables:
            logger.info(f"Reached max tables limit ({max_tables}), skipping remaining {total_tables - max_tables} tables")
        
        for table in tables:
            table_text = table.get_text(' | ', strip=True)
            if not table_text:
                continue
            
            # Check if we should analyze this table
            should_analyze, reason = media_analyzer.should_analyze_table(table)
            
            if should_analyze:
                logger.info(f"🔍 Analyzing table: {reason}")
                description = media_analyzer.generate_table_description(table, table_text)
                if description:
                    logger.info(f"✅ Generated table description: {description[:100]}...")
                else:
                    logger.warning(f"⚠️ LLM table description failed, using fallback")
                    description = DocumentProcessor.describe_table(table_text)
            else:
                logger.debug(f"⏭️ Skipping table analysis: {reason}")
                description = DocumentProcessor.describe_table(table_text)
            
            # Extract ALL table headers and rows (not just first 50)
            headers = []
            rows_data = []
            thead = table.find('thead')
            if thead:
                for th in thead.find_all(['th', 'td']):
                    headers.append(th.get_text(strip=True))
            if not headers:
                first_row = table.find('tr')
                if first_row:
                    for th in first_row.find_all(['th', 'td']):
                        headers.append(th.get_text(strip=True))
            
            # Extract ALL rows for storage
            for tr in table.find_all('tr'):
                cells = []
                for td in tr.find_all(['td', 'th']):
                    cells.append(td.get_text(strip=True))
                if cells:
                    rows_data.append(cells)
            
            # Get row limits from settings
            max_rows_to_store = getattr(crawler_settings, 'MAX_TABLE_ROWS_TO_STORE', 50)
            max_rows_to_text = getattr(crawler_settings, 'MAX_TABLE_ROWS_TO_TEXT', 50)
            
            assets.append({
                'media_type': 'table',
                'source_url': page_url,
                'mime_type': 'text/html',
                'data_base64': base64.b64encode(str(table).encode('utf-8')).decode('ascii'),
                'description': description,
                'was_analyzed': should_analyze,
                'table_headers': headers,
                'table_rows': rows_data[:max_rows_to_store],  # Use setting for storage
                'table_rows_full': rows_data,  # Store ALL rows
                'table_summary': description,
                'row_count': len(rows_data),
            })
        
        return assets


def _infer_content_type_from_url(url: str) -> Optional[ContentType]:
    """Infer content type from URL patterns."""
    if not url:
        return None
    if '/docs/' in url or '/documentation/' in url or '/api/' in url or '/reference/' in url:
        return ContentType.DOCUMENTATION
    if '/blog/' in url or '/news/' in url or '/post/' in url or '/article/' in url:
        return ContentType.ARTICLE
    if '/product/' in url or '/item/' in url or '/p/' in url or '/shop/' in url:
        return ContentType.ECOMMERCE
    if '/pricing/' in url or '/compare/' in url or '/features/' in url:
        return ContentType.DATA_TABLE
    return None


@dramatiq.actor(
    actor_name="workers.processor_worker.process_document",
    queue_name=PROCESSING_QUEUE_NAME,
    max_retries=2,
    time_limit=600000
)
def process_document(chat_id: str, document_id: str):
    """Process raw HTML document with enhanced content extraction and status tracking"""
    print(f"📄 Processing document: {document_id} for chat: {chat_id}")
    
    # ✅ Get progress tracker
    tracker = get_progress_tracker(chat_id)
    
    try:
        # ✅ Update status: processing started
        tracker.update_stage('processing', 0, "Extracting content from HTML...")

        # Get document
        doc = execute_one(
            """SELECT d.id, d.page_version_id, d.content, d.metadata, d.processing_status, p.url
                FROM documents d
                JOIN page_versions pv ON pv.id = d.page_version_id
                JOIN pages p ON p.id = pv.page_id
                WHERE d.id = %s""",
            (document_id,)
        )
        
        if not doc:
            error_msg = f"Document not found: {document_id}"
            print(f"❌ {error_msg}")
            tracker.mark_failed(error_msg)
            return
        
        html = doc['content']
        
        # Parse metadata
        metadata_raw = doc['metadata']
        if isinstance(metadata_raw, str):
            try:
                metadata = json.loads(metadata_raw) if metadata_raw else {}
            except json.JSONDecodeError:
                metadata = {}
        else:
            metadata = metadata_raw or {}
        
        url = metadata.get('url') or doc.get('url') or ''
        print(f"📋 Processing HTML for: {url}")
        print(f"📄 Raw HTML size: {len(html)} chars")

        # ✅ Update progress: content extraction
        tracker.update_stage('processing', 20, "Cleaning and extracting content...")
        
        # Check if already processed
        doc_status = doc.get('processing_status')
        if doc_status == 'COMPLETED':
            # Document already processed — skip reprocessing but still
            # forward to chunker so pending_documents gets decremented.
            print(f"⚠️ Document {document_id} already processed, forwarding to chunker")
            tracker.update_stage('chunking', 0, "Content already extracted, creating chunks...")
            enqueue_worker(
                "workers.chunker_worker.chunk_document",
                chat_id,
                document_id,
            )
            return

        # If it's already processing, skip (avoid duplicate work).
        # The active worker will reach the chunker naturally.
        if doc_status == 'PROCESSING':
            print(f"⚠️ Document {document_id} is already being processed")
            return
        
        # Update status to processing
        now = get_current_datetime().isoformat()
        execute_update(
            "UPDATE documents SET processing_status = 'PROCESSING', updated_at = %s WHERE id = %s",
            (now, document_id)
        )
        
        # Step 1: Extract metadata from HTML
        extracted_metadata = DocumentProcessor.extract_metadata_from_html(html)

        # Step 2: Process content structure
        processor_result = ContentProcessor.process_html(
            html,
            source_url=url,
            page_title=(
                metadata.get('title') or
                metadata.get('og_title') or
                extracted_metadata.get('title') or
                url or
                'Untitled Page'
            )
        )

        print("Processor result keys:", processor_result)

        # raw_content = processor_result.get('all_text', '')
        raw_content = processor_result.get('main_content', {}).get('all_text', '')
        print(f"📄 Raw HTML length: {len(raw_content)} chars")

        document_structure = processor_result.get('document_structure', {})
        section_count = len(document_structure.get('sections', []))
        table_count = len(document_structure.get('tables', []))
        print(f"📊 Document structure: {section_count} sections, {table_count} tables")

        # Log sample of extracted content for debugging
        if raw_content:
            sample = raw_content[:500] + "..." if len(raw_content) > 500 else raw_content
            print(f"🧾 Raw HTML sample: {sample}")

        # ✅ Update progress: media extraction
        tracker.update_stage('processing', 50, "Extracting images and tables...")

        # Step 3: Extract media assets with intelligent analysis
        media_assets = DocumentProcessor.extract_media(html, url)
        
        # Log media processing stats
        analyzed_images = sum(1 for a in media_assets if a.get('was_analyzed') and a['media_type'] == 'image')
        analyzed_tables = sum(1 for a in media_assets if a.get('was_analyzed') and a['media_type'] == 'table')
        print(f"📊 Media assets: {len(media_assets)} total (Images analyzed: {analyzed_images}, Tables analyzed: {analyzed_tables})")
        
        # ✅ Step 3.5: Create dedicated media chunks
        try:
            # Create media chunks using the ContentProcessor's media chunking method
            media_chunks = ContentProcessor._create_media_chunks(
                media_assets, 
                metadata.get('title') or extracted_metadata.get('title') or url or 'Untitled Page',
                url
            )
            
            if media_chunks:
                logger.info(f"✅ Created {len(media_chunks)} dedicated media chunks")
                
                # Merge media chunks into the document structure's sections
                if 'sections' not in document_structure:
                    document_structure['sections'] = []
                
                # Add media chunks as special sections
                document_structure['sections'].extend(media_chunks)
                document_structure['media_chunks'] = media_chunks
                
                # Also add to raw_content for search
                for chunk in media_chunks:
                    if chunk.get('content'):
                        raw_content += f"\n\n{chunk['content']}"
                
                logger.info(f"📊 Added {len(media_chunks)} media chunks to document structure")
        except Exception as e:
            logger.warning(f"Failed to create media chunks: {e}")
            # Continue without media chunks - not critical
        
        # ✅ Step 4: Infer content type for type-aware chunking
        content_type = _infer_content_type_from_url(url)
        
        # Use ContentClassifier for more accurate classification if available
        try:
            from bs4 import BeautifulSoup
            classifier = ContentClassifier()
            soup = BeautifulSoup(html, 'html.parser')
            detected_type = classifier.classify(soup, url)
            if detected_type:
                content_type = detected_type
                print(f"📋 ContentClassifier detected: {content_type.value}")
        except Exception as e:
            logger.warning(f"ContentClassifier failed, using URL inference: {e}")
        
        # Store content_type in metadata
        if content_type:
            extracted_metadata['content_type'] = content_type.value
        
        # Process media assets to add to raw_content
        for asset in media_assets:
            raw_content += f"\n\nMedia asset: {asset['media_type']} - {asset['description']}"
            
            # Also add structured data to raw_content for search
            if asset.get('structured_data'):
                structured = asset['structured_data']
                if structured.get('visible_text'):
                    raw_content += f"\nVisible text: {structured['visible_text']}"
                if structured.get('entities'):
                    for entity in structured.get('entities', []):
                        raw_content += f"\nEntity: {entity.get('name', '')} ({entity.get('type', '')}) - {entity.get('value', '')}"
            
            # Add table data if present
            if asset['media_type'] == 'table' and asset.get('table_headers') and asset.get('table_rows'):
                raw_content += f"\nTable headers: {', '.join(asset['table_headers'])}"
                # Use MAX_TABLE_ROWS_TO_TEXT for searchable content
                max_rows_to_text = getattr(crawler_settings, 'MAX_TABLE_ROWS_TO_TEXT', 50)
                table_rows_for_text = asset.get('table_rows', [])[:max_rows_to_text]
                raw_content += f"\nTable rows: {table_rows_for_text}"

        # ✅ Step 5: Merge metadata with enhanced document structure
        merged_metadata = {
            **metadata,
            **extracted_metadata,
            'document_structure': document_structure,  # Now includes media_chunks
            'url': url,
            'extraction_version': '2.0',
            'media_analysis_stats': {
                'total_assets': len(media_assets),
                'analyzed_images': analyzed_images,
                'analyzed_tables': analyzed_tables,
                'media_chunks_created': len(document_structure.get('media_chunks', [])),
            },
            'content_type': content_type.value if content_type else None,
        }

        # Step 6: Update document with content_type and enhanced structure
        execute_update(
            """UPDATE documents 
               SET cleaned_content = %s, 
                   metadata = %s::jsonb,
                   processing_status = 'COMPLETED',
                   processed_at = %s,
                   updated_at = %s,
                   content_type = %s
               WHERE id = %s""",
            (raw_content, json.dumps(merged_metadata), now, now, 
             content_type.value if content_type else None, document_id)
        )

        # Step 7: Store media assets with enhanced metadata
        for asset in media_assets:
            # Determine processing_status
            processing_status = 'COMPLETED' if asset.get('was_analyzed') else 'SKIPPED'
            vision_model = 'gemma4:31b-cloud' if asset.get('was_analyzed') else None
            
            # Prepare table-specific fields
            table_headers = None
            table_rows = None
            table_summary = None
            
            if asset['media_type'] == 'table':
                table_headers = json.dumps(asset.get('table_headers', []))
                # Store ALL rows (not just first 50)
                table_rows = json.dumps(asset.get('table_rows_full', asset.get('table_rows', [])))
                table_summary = asset.get('description')
            
            execute_update(
                """INSERT INTO media_assets
                (page_version_id, document_id, media_type, source_url,
                 mime_type, data_base64, description, created_at,
                 image_type, alt_text, caption, surrounding_text, 
                 section_heading, processing_status, vision_model,
                 table_headers, table_rows, table_summary)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    doc['page_version_id'], 
                    document_id, 
                    asset['media_type'],
                    asset['source_url'], 
                    asset['mime_type'], 
                    asset['data_base64'],
                    asset['description'], 
                    now,
                    asset.get('image_type', 'unknown') if asset['media_type'] == 'image' else None,
                    asset.get('alt_text', '') if asset['media_type'] == 'image' else None,
                    asset.get('caption') if asset['media_type'] == 'image' else None,
                    asset.get('surrounding_text') if asset['media_type'] == 'image' else None,
                    asset.get('section_heading') if asset['media_type'] == 'image' else None,
                    processing_status,
                    vision_model,
                    table_headers,
                    table_rows,
                    table_summary
                )
            )

        print(f"✅ Document {document_id} processed successfully")
        print(f"   - Raw HTML length: {len(raw_content)} chars")
        print(f"   - Media assets: {len(media_assets)}")
        print(f"   - Media chunks created: {len(document_structure.get('media_chunks', []))}")
        if content_type:
            print(f"   - Content type: {content_type.value}")
        
        # ✅ Update progress: processing complete, move to chunking with content type
        tracker.update_stage('chunking', 0, "Creating chunks from content...")

        # ✅ Enqueue chunking job with chat_id and document_id
        enqueue_worker(
            "workers.chunker_worker.chunk_document",
            chat_id,
            document_id,
        )
        
    except Exception as e:
        error_msg = f"Error processing document: {str(e)}"
        print(f"❌ {error_msg}")
        import traceback
        traceback.print_exc()
        
        # Update document status
        execute_update(
            "UPDATE documents SET processing_status = 'FAILED', updated_at = %s WHERE id = %s",
            (get_current_datetime().isoformat(), document_id)
        )
        
        # ✅ Mark chat as failed using progress tracker
        tracker.mark_failed(error_msg)
        raise


print("✅ Processor worker registered")
print(f"📋 Listening on queue: {PROCESSING_QUEUE_NAME}")