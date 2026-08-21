# workers/processor_worker.py
import dramatiq
import logging
import json
import re
from typing import Dict, Any
from database_sync import execute_query, execute_update, execute_one
from utils.helpers import get_current_datetime
from redis_config import PROCESSING_QUEUE_NAME

logger = logging.getLogger(__name__)

try:
    from bs4 import BeautifulSoup
    import markdownify
    print("✅ Processor dependencies imported successfully")
except Exception as e:
    print(f"❌ Failed to import processor dependencies: {e}")
    raise


class DocumentProcessor:
    """Clean and process raw HTML into Markdown"""
    
    @staticmethod
    def clean_html(html: str) -> str:
        """Clean HTML and extract main content"""
        soup = BeautifulSoup(html, 'html.parser')
        
        for tag in soup(['script', 'style', 'noscript', 'iframe', 'header', 'footer', 'nav']):
            tag.decompose()
        
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
        ]
        
        for selector in boilerplate_selectors:
            for element in soup.select(selector):
                element.decompose()
        
        return str(soup)
    
    @staticmethod
    def html_to_markdown(html: str) -> str:
        """Convert HTML to Markdown"""
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
            md = md.strip()
            
            return md
        except Exception as e:
            logger.error(f"Error converting HTML to markdown: {e}")
            return html
    
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
        
        return metadata


@dramatiq.actor(
    queue_name=PROCESSING_QUEUE_NAME,
    max_retries=2,
    time_limit=600000
)
def process_document(document_id: str):
    """Process raw HTML document (SYNC version)"""
    print(f"📄 Processing document: {document_id}")
    
    try:
        # Get document
        doc = execute_one(
            "SELECT id, page_version_id, content, metadata FROM documents WHERE id = %s",
            (document_id,)
        )
        
        if not doc:
            print(f"❌ Document {document_id} not found")
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
        
        url = metadata.get('url', '')
        print(f"📋 Processing HTML for: {url}")
        
        # Check if already processed
        if doc.get('processing_status') == 'COMPLETED':
            print(f"⚠️ Document {document_id} already processed")
            return
        
        # Update status to processing
        now = get_current_datetime().isoformat()
        execute_update(
            "UPDATE documents SET processing_status = 'PROCESSING', updated_at = %s WHERE id = %s",
            (now, document_id)
        )
        
        # Clean and convert
        markdown_content = DocumentProcessor.html_to_markdown(html)
        
        # Extract metadata from HTML
        extracted_metadata = DocumentProcessor.extract_metadata_from_html(html)
        
        # Merge metadata
        merged_metadata = {**metadata, **extracted_metadata}
        
        # Update document
        execute_update(
            """UPDATE documents 
               SET cleaned_content = %s, 
                   metadata = %s::jsonb,
                   processing_status = 'COMPLETED',
                   processed_at = %s,
                   updated_at = %s
               WHERE id = %s""",
            (markdown_content, json.dumps(merged_metadata), now, now, document_id)
        )
        
        print(f"✅ Document {document_id} processed successfully ({len(markdown_content)} chars)")
        
        # Enqueue chunking job
        from workers.chunker_worker import chunk_document
        chunk_document.send(document_id)
        
    except Exception as e:
        print(f"❌ Error processing document {document_id}: {e}")
        import traceback
        traceback.print_exc()
        
        execute_update(
            "UPDATE documents SET processing_status = 'FAILED', updated_at = %s WHERE id = %s",
            (get_current_datetime().isoformat(), document_id)
        )
        raise


print("✅ Processor worker registered")
print(f"📋 Listening on queue: {PROCESSING_QUEUE_NAME}")