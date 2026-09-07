# workers/chunker_worker.py
import os
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
from utils.chat_status_tracker import ChatStatusTracker
from utils.progress_tracker import get_progress_tracker

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
def chunk_document(chat_id: str, document_id: str):
    """
    Chunk processed document using new EnhancedChunker with structure-aware logic.
    Uses document_structure from metadata for semantic understanding.
    """
    print(f"📦 Chunking document with EnhancedChunker: {document_id} for chat: {chat_id}")
    
    # Get progress tracker
    tracker = get_progress_tracker(chat_id)
    
    try:
        # Update status: chunking started
        tracker.update_stage('chunking', 0, "Splitting content into chunks...")

        # Get document
        doc = execute_one(
            "SELECT id, page_version_id, cleaned_content, metadata FROM documents WHERE id = %s",
            (document_id,)
        )
        
        if not doc:
            error_msg = f"Document not found: {document_id}"
            print(f"❌ {error_msg}")
            tracker.mark_failed(error_msg)
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
        
        # ✅ FIX: Check if this document already has chunks and their embedding status
        existing_chunks = execute_query(
            """SELECT id, embedding_status, chunk_index 
               FROM chunks 
               WHERE document_id = %s 
               ORDER BY chunk_index""",
            (document_id,)
        )
        
        if existing_chunks:
            # ✅ Check if all existing chunks are embedded
            pending_chunks = [c for c in existing_chunks if c.get('embedding_status') == 'PENDING']
            processing_chunks = [c for c in existing_chunks if c.get('embedding_status') == 'PROCESSING']
            completed_chunks = [c for c in existing_chunks if c.get('embedding_status') == 'COMPLETED']
            failed_chunks = [c for c in existing_chunks if c.get('embedding_status') == 'FAILED']
            
            print(f"📊 Document {document_id} has {len(existing_chunks)} chunks: "
                  f"{len(completed_chunks)} completed, {len(pending_chunks)} pending, "
                  f"{len(processing_chunks)} processing, {len(failed_chunks)} failed")
            
            # ✅ If there are pending or processing chunks, send to embedder
            if pending_chunks or processing_chunks:
                print(f"📤 Sending {len(pending_chunks)} pending chunks to embedder")
                tracker.update_stage('embedding', 0, f"Generating embeddings for {len(pending_chunks)} chunks...")
                from workers.embedder_worker import embed_chunks
                embed_chunks.send(chat_id, document_id)
                return
            
            # ✅ If all chunks are completed, mark as completed
            if completed_chunks and not pending_chunks and not processing_chunks and not failed_chunks:
                tracker.mark_completed(f"All {len(completed_chunks)} chunks already embedded")
                return
            
            # ✅ If there are failed chunks and no pending ones, handle partial failure
            if failed_chunks and not pending_chunks and not processing_chunks:
                if completed_chunks:
                    # Partial success - mark completed with warning
                    tracker.mark_completed(f"Embedded {len(completed_chunks)} chunks, {len(failed_chunks)} failed")
                    # Store failure count in metadata
                    execute_update(
                        """UPDATE chats 
                           SET metadata = jsonb_set(COALESCE(metadata, '{}'::jsonb), '{embedding_failures}', %s) 
                           WHERE id = %s""",
                        (json.dumps(len(failed_chunks)), chat_id)
                    )
                else:
                    # All chunks failed
                    tracker.mark_failed(f"All {len(failed_chunks)} chunks failed to embed")
                return
        
        # Extract structure from metadata (created by ContentProcessor in processor_worker)
        document_structure = metadata.get('document_structure', {})
        full_structure = {
            'page_title': metadata.get('page_title') or document_structure.get('page_title', ''),
            'source_url': metadata.get('url') or document_structure.get('source_url', ''),
            'main_content': {
                'sections': document_structure.get('sections', []),
                'tables': document_structure.get('tables', []),
                'lists': document_structure.get('lists', []),
                'all_text': metadata.get('all_text', ''),
                'has_content': bool(document_structure.get('sections')),
            },
            'ui_summary': metadata.get('ui_summary', []),
        }

        if not document_structure:
            print(f"⚠️ No document_structure found in metadata, falling back to plain text chunking")
            # Fallback: use cleaned_content if structure is missing
            content = doc.get('cleaned_content', '')
            if not content:
                error_msg = "No content available for document"
                print(f"⚠️ {error_msg}")
                tracker.mark_failed(error_msg)
                return
            
            # Use cleaned_content for plain text chunking
            chunker = SemanticChunker(chunk_size=500, chunk_overlap=50)
            chunks = chunker.chunk_text(content)
        else:
            url = metadata.get('url', '')
            print(f"📋 Using structured content for: {url}")
            print(f"📊 Structure contains: {len(document_structure.get('sections', []))} sections, {len(document_structure.get('tables', []))} tables, {len(document_structure.get('cards', []))} cards")
            
            # Update progress: analyzing structure
            tracker.update_stage('chunking', 30, "Analyzing document structure...")
            
            # 🎯 USE NEW ENHANCED CHUNKER
            chunks = EnhancedChunker.chunk_structure(full_structure)
            print(f"✨ EnhancedChunker created {len(chunks)} structure-aware chunks")
        
        # Update progress: storing chunks
        tracker.update_stage('chunking', 60, f"Storing {len(chunks)} chunks...")
        
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
        
        # ✅ FIX: Only move to embedding if we have new chunks
        if chunk_ids:
            # Update status: chunking complete, move to embedding
            tracker.update_stage('embedding', 0, f"Generating embeddings for {len(chunks)} chunks...")
            
            # Enqueue embedding job
            from workers.embedder_worker import embed_chunks
            embed_chunks.send(chat_id, document_id)
            print(f"📤 Sent {len(chunk_ids)} chunks to embedder")
        else:
            # ✅ FIX: Check if there are any existing chunks that need embedding
            existing_chunks = execute_query(
                """SELECT id, embedding_status 
                   FROM chunks 
                   WHERE document_id = %s""",
                (document_id,)
            )
            
            if existing_chunks:
                pending = [c for c in existing_chunks if c.get('embedding_status') == 'PENDING']
                processing = [c for c in existing_chunks if c.get('embedding_status') == 'PROCESSING']
                failed = [c for c in existing_chunks if c.get('embedding_status') == 'FAILED']
                completed = [c for c in existing_chunks if c.get('embedding_status') == 'COMPLETED']
                
                if pending or processing:
                    # Send to embedder if there are pending chunks
                    print(f"📤 Found {len(pending)} pending chunks, sending to embedder")
                    tracker.update_stage('embedding', 0, f"Generating embeddings for {len(pending)} chunks...")
                    from workers.embedder_worker import embed_chunks
                    embed_chunks.send(chat_id, document_id)
                    return
                elif failed and not pending and not processing:
                    if completed:
                        tracker.mark_completed(f"Embedded {len(completed)} chunks, {len(failed)} failed")
                    else:
                        tracker.mark_failed(f"All {len(failed)} chunks failed to embed")
                    return
                elif completed:
                    tracker.mark_completed(f"All {len(completed)} chunks already embedded")
                    return
            
            # If we get here, there are truly no chunks
            print(f"⚠️ No chunks found for document {document_id}")
            tracker.mark_failed("No chunks were created for this document")
        
        return chunk_ids
        
    except Exception as e:
        error_msg = f"Error chunking document: {str(e)}"
        print(f"❌ {error_msg}")
        import traceback
        traceback.print_exc()
        
        # Mark chat as failed using progress tracker
        tracker.mark_failed(error_msg)
        raise


print("✅ Chunker worker registered with EnhancedChunker")
print(f"📋 Listening on queue: {CHUNKING_QUEUE_NAME}")