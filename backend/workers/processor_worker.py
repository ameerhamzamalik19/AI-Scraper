# workers/processor_worker.py
import dramatiq
import logging
import json
import re
import base64
import mimetypes
import os
from urllib.parse import urljoin, urlparse
from typing import Dict, Any
import requests
from openai import OpenAI
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

    @staticmethod
    def _image_data(source_url: str) -> tuple[str, str]:
        """Download an image and return its MIME type and base64 payload."""
        if source_url.startswith('data:'):
            header, payload = source_url.split(',', 1)
            mime_type = header[5:].split(';', 1)[0] or 'application/octet-stream'
            return mime_type, payload

        response = requests.get(source_url, timeout=30)
        response.raise_for_status()
        if len(response.content) > 10 * 1024 * 1024:
            raise ValueError('image exceeds 10 MB limit')
        mime_type = response.headers.get('content-type', '').split(';', 1)[0]
        mime_type = mime_type or mimetypes.guess_type(source_url)[0] or 'application/octet-stream'
        return mime_type, base64.b64encode(response.content).decode('ascii')

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
        """Generate a searchable description while preserving table values."""
        api_key = os.getenv('GROQ_API_KEY')
        if not api_key:
            return f'Table data: {table_text}'
        try:
            client = OpenAI(base_url='https://api.groq.com/openai/v1', api_key=api_key)
            response = client.chat.completions.create(
                model='groq/compound-mini',
                messages=[{'role': 'user', 'content': f'Summarize this table for semantic search. Preserve labels, numbers, relationships, and key conclusions. Return only factual text.\n\n{table_text}'}],
                temperature=0.1,
                max_tokens=800
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.warning('Table description generation failed: %s', e)
            return f'Table data: {table_text}'

    @staticmethod
    def extract_media(html: str, page_url: str) -> list[Dict[str, Any]]:
        """Extract images and tables, preserving originals and descriptions."""
        soup = BeautifulSoup(html, 'html.parser')
        assets = []
        for image in soup.find_all('img')[:20]:
            source = image.get('src') or image.get('data-src')
            if not source:
                continue
            source_url = urljoin(page_url, source)
            try:
                mime_type, payload = DocumentProcessor._image_data(source_url)
                description = DocumentProcessor.describe_image(
                    source_url,
                    image.get('alt', ''),
                    image.get('title', '')
                )
                assets.append({'media_type': 'image', 'source_url': source_url,
                               'mime_type': mime_type, 'data_base64': payload,
                               'description': description})
            except Exception as e:
                logger.warning('Unable to process image %s: %s', source_url, e)

        for table in soup.find_all('table')[:20]:
            table_text = table.get_text(' | ', strip=True)
            if not table_text:
                continue
            assets.append({'media_type': 'table', 'source_url': page_url,
                           'mime_type': 'text/html',
                           'data_base64': base64.b64encode(str(table).encode('utf-8')).decode('ascii'),
                           'description': DocumentProcessor.describe_table(table_text)})
        return assets


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
                """SELECT d.id, d.page_version_id, d.content, d.metadata, p.url
                    FROM documents d
                    JOIN page_versions pv ON pv.id = d.page_version_id
                    JOIN pages p ON p.id = pv.page_id
                    WHERE d.id = %s""",
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
        
        url = metadata.get('url') or doc.get('url') or ''
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
        media_assets = DocumentProcessor.extract_media(html, url)
        for asset in media_assets:
            markdown_content += f"\n\n## {asset['media_type'].title()}\n\n{asset['description']}"
        
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

        for asset in media_assets:
            execute_update(
                """INSERT INTO media_assets
                   (page_version_id, document_id, media_type, source_url,
                    mime_type, data_base64, description, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (doc['page_version_id'], document_id, asset['media_type'],
                 asset['source_url'], asset['mime_type'], asset['data_base64'],
                 asset['description'], now)
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