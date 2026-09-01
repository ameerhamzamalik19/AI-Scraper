# workers/chunker_worker.py
import dramatiq
import hashlib
import json
import re
import uuid
from typing import List, Dict, Any
from database_sync import execute_query, execute_update, execute_one
from utils.helpers import get_current_datetime
from redis_config import CHUNKING_QUEUE_NAME
from processors.chunker import EnhancedChunker

print("✅ Chunker worker loaded with EnhancedChunker")


class SemanticChunker:
    """
    Split text into semantic chunks using heading hierarchy and paragraphs.
    """
    
    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
    
    def extract_headings(self, markdown: str) -> List[Dict[str, Any]]:
        """Extract headings and their levels from markdown"""
        headings = []
        pattern = r'^(#{1,6})\s+(.+)$'
        
        pos = 0
        for line in markdown.split('\n'):
            line_stripped = line.strip()
            match = re.match(pattern, line_stripped)
            if match:
                level = len(match.group(1))
                text = match.group(2).strip()
                headings.append({
                    'level': level,
                    'text': text,
                    'position': pos
                })
            pos += len(line) + 1
        
        return headings
    
    def get_heading_path(self, headings: List[Dict], position: int) -> List[str]:
        """Get the heading hierarchy at a given position"""
        path = []
        for heading in headings:
            if heading['position'] <= position:
                path = path[:heading['level'] - 1]
                path.append(heading['text'])
        return path

    @classmethod
    def remove_boilerplate_lines(cls, text: str) -> str:
        """Remove standalone footer and navigation lines before chunking."""
        boilerplate_patterns = (
            r'^©?\s*[^\n]*all rights reserved\s*$',
            r'^\s*(follow us|contact us|privacy policy|terms of service)\s*$',
            r'^\s*\[?(follow us|privacy policy|terms of service)\]?\s*$',
        )
        lines = [
            line for line in text.split('\n')
            if not any(re.search(pattern, line.strip(), re.IGNORECASE) for pattern in boilerplate_patterns)
        ]
        return '\n'.join(lines)
    
    def chunk_text(self, text: str) -> List[Dict[str, Any]]:
        """Chunk text into semantic chunks with heading context"""
        text = self.remove_boilerplate_lines(text)
        chunks = []
        
        lines = text.split('\n')
        current_heading = None
        current_content = []
        headings = self.extract_headings(text)
        
        pos = 0
        for i, line in enumerate(lines):
            line_stripped = line.strip()
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', line_stripped)
            
            if heading_match:
                if current_content:
                    content = '\n'.join(current_content).strip()
                    if content:
                        heading_path = self.get_heading_path(headings, pos)
                        chunks.append({
                            'content': content,
                            'heading_path': heading_path,
                            'heading': current_heading
                        })
                
                current_heading = heading_match.group(2)
                current_content = []
            else:
                if line_stripped or current_content:
                    current_content.append(line)
            
            pos += len(line) + 1
        
        if current_content:
            content = '\n'.join(current_content).strip()
            if content:
                heading_path = self.get_heading_path(headings, pos)
                chunks.append({
                    'content': content,
                    'heading_path': heading_path,
                    'heading': current_heading
                })
        
        if not chunks:
            paragraphs = text.split('\n\n')
            for para in paragraphs:
                if para.strip():
                    chunks.append({
                        'content': para.strip(),
                        'heading_path': [],
                        'heading': None
                    })
        
        final_chunks = []
        for chunk in chunks:
            content = chunk['content']
            if len(content.split()) > self.chunk_size * 1.5:
                sub_chunks = self._split_long_chunk(content, chunk['heading_path'])
                final_chunks.extend(sub_chunks)
            else:
                final_chunks.append(chunk)
        
        for i, chunk in enumerate(final_chunks):
            chunk['chunk_index'] = i
            chunk['token_count'] = len(chunk['content'].split())
        
        return self.filter_chunks(final_chunks)
    
    def _split_long_chunk(self, content: str, heading_path: List[str]) -> List[Dict[str, Any]]:
        """Split a long chunk into smaller pieces"""
        words = content.split()
        chunks = []
        
        for i in range(0, len(words), self.chunk_size - self.chunk_overlap):
            chunk_words = words[i:i + self.chunk_size]
            chunk_content = ' '.join(chunk_words)
            chunks.append({
                'content': chunk_content,
                'heading_path': heading_path,
                'heading': heading_path[-1] if heading_path else None
            })
        
        return chunks

    @staticmethod
    def _normalize_content(content: str) -> str:
        """Normalize text for boilerplate detection and deduplication."""
        return re.sub(r'\s+', ' ', content).strip().lower()

    @classmethod
    def _is_useful_chunk(cls, content: str) -> bool:
        """Reject short navigation, footer, and other boilerplate fragments."""
        normalized = cls._normalize_content(content)
        if len(normalized.split()) < 8 or len(normalized) < 40:
            return False

        boilerplate_patterns = (
            r'^©?\s*[^\n]*all rights reserved$',
            r'^(follow us|contact us|privacy policy|terms of service)$',
            r'^\[?(follow us|privacy policy|terms of service)\]?\b',
            r'^(home|about us|services|company|menu|navigation)$',
        )
        return not any(re.search(pattern, normalized) for pattern in boilerplate_patterns)

    def filter_chunks(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Keep useful, unique chunks before they are persisted or embedded."""
        filtered = []
        seen = set()
        for chunk in chunks:
            content_key = self._normalize_content(chunk['content'])
            if not self._is_useful_chunk(chunk['content']) or content_key in seen:
                continue
            seen.add(content_key)
            filtered.append(chunk)
        return filtered


def chunk_exists(page_version_id: str, chunk_hash: str) -> bool:
    """Check if a chunk with the same hash already exists for this page version"""
    result = execute_one(
        "SELECT id FROM chunks WHERE page_version_id = %s AND chunk_hash = %s",
        (page_version_id, chunk_hash)
    )
    return result is not None


@dramatiq.actor(
    queue_name=CHUNKING_QUEUE_NAME,
    max_retries=2,
    time_limit=600000
)
def chunk_document(document_id: str):
    """
    Chunk processed document using new EnhancedChunker with structure-aware logic.
    Uses document_structure from metadata for semantic understanding.
    """
    print(f"📦 Chunking document with EnhancedChunker: {document_id}")
    
    try:
        # Get document
        doc = execute_one(
            "SELECT id, page_version_id, cleaned_content, metadata FROM documents WHERE id = %s",
            (document_id,)
        )
        
        if not doc:
            print(f"❌ Document {document_id} not found")
            return
        
        page_version_id = doc['page_version_id']
        
        # Parse metadata to get document_structure
        metadata_raw = doc['metadata']
        if isinstance(metadata_raw, str):
            try:
                metadata = json.loads(metadata_raw) if metadata_raw else {}
            except json.JSONDecodeError:
                metadata = {}
        else:
            metadata = metadata_raw or {}
        
        # Check if this document already has chunks (to avoid reprocessing)
        existing_chunks_count = execute_one(
            "SELECT COUNT(*) as count FROM chunks WHERE document_id = %s",
            (document_id,)
        )
        
        if existing_chunks_count and existing_chunks_count['count'] > 0:
            print(f"📊 Document {document_id} already has {existing_chunks_count['count']} chunks. Skipping reprocessing.")
            return
        
        # Extract structure from metadata (created by ContentProcessor in processor_worker)
        document_structure = metadata.get('document_structure', {})
        
        if not document_structure:
            print(f"⚠️ No document_structure found in metadata, falling back to plain text chunking")
            # Fallback: use cleaned_content if structure is missing
            content = doc.get('cleaned_content', '')
            if not content:
                print(f"⚠️ No content available for document {document_id}")
                return
            document_structure = metadata.get('document_structure', {})

            if not document_structure:
                print(f"⚠️ No document_structure in metadata for {document_id} — using plain text fallback")
                content = doc.get('cleaned_content', '') or ''
                # cleaned_content is already plain text — don't re-parse as HTML
                chunker = SemanticChunker(chunk_size=500, chunk_overlap=50)
                chunks = chunker.chunk_text(content)
            else:
                chunks = EnhancedChunker.chunk_structure(document_structure)
            # if not plain_text:
            #     print(f"⚠️ No plain text available for document {document_id}")
            #     return
            # chunker = SemanticChunker(chunk_size=500, chunk_overlap=50)
            # chunks = chunker.chunk_text(plain_text)
        else:
            url = metadata.get('url', '')
            print(f"📋 Using structured content for: {url}")
            print(f"📊 Structure contains: {len(document_structure.get('sections', []))} sections, {len(document_structure.get('tables', []))} tables, {len(document_structure.get('cards', []))} cards")
            
            # 🎯 USE NEW ENHANCED CHUNKER
            chunks = EnhancedChunker.chunk_structure(document_structure)
            print(f"✨ EnhancedChunker created {len(chunks)} structure-aware chunks")
        
        # Store chunks with ON CONFLICT to handle duplicates gracefully
        now = get_current_datetime().isoformat()
        chunk_ids = []
        skipped_count = 0
        inserted_count = 0
        
        for chunk_data in chunks:
            chunk_id = str(uuid.uuid4())
            content = chunk_data.get('content', '')
            content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
            chunk_index = chunk_data.get('chunk_index', 0)
            
            heading_path = json.dumps(chunk_data.get('heading_path', []))
            chunk_type = chunk_data.get('chunk_type', 'text')
            
            # Map new chunk types to schema-compatible ones
            chunk_type_map = {
                'section': 'text',
                'table': 'table',
                'card': 'mixed',
                'paragraph_group': 'text',
            }
            db_chunk_type = chunk_type_map.get(chunk_type, 'text')
            
            # Use ON CONFLICT to skip duplicates without error
            result = execute_update(
                """INSERT INTO chunks 
                   (id, page_version_id, document_id, chunk_index, chunk_type, 
                    content, heading_path, token_count, chunk_hash,
                    embedding_status, created_at, updated_at) 
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (document_id, chunk_index) DO NOTHING""",
                (
                    chunk_id,
                    page_version_id,
                    document_id,
                    chunk_index,
                    db_chunk_type,
                    content,
                    heading_path,
                    chunk_data.get('token_count', 0),
                    content_hash,
                    'PENDING',
                    now,
                    now
                )
            )
            
            # Check if insert was successful (result is the number of rows affected)
            if result == 0:
                skipped_count += 1
                print(f"⏭️ Skipped duplicate chunk index {chunk_index}")
            else:
                inserted_count += 1
                chunk_ids.append(chunk_id)
        
        print(f"✅ Document {document_id}: inserted {inserted_count} chunks, skipped {skipped_count} duplicates")
        
        # Update document status to CHUNKED
        # execute_update(
        #     "UPDATE documents SET processing_status = 'CHUNKED', updated_at = %s WHERE id = %s",
        #     (now, document_id)
        # )
        
        # Enqueue embedding job
        if chunk_ids:
            from workers.embedder_worker import embed_chunks
            embed_chunks.send(chunk_ids)
            print(f"📤 Sent {len(chunk_ids)} chunks to embedder")
        else:
            print(f"⚠️ No new chunks to embed for document {document_id}")
        
        return chunk_ids
        
    except Exception as e:
        print(f"❌ Error chunking document {document_id}: {e}")
        import traceback
        traceback.print_exc()
        raise


print("✅ Chunker worker registered with EnhancedChunker")
print(f"📋 Listening on queue: {CHUNKING_QUEUE_NAME}")