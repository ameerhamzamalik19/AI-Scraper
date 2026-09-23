# workers/chunker_worker.py
import os
import dramatiq
import hashlib
import json
import re
import uuid
from typing import List, Dict, Any, Optional

from database_sync import execute_query, execute_update, execute_one
from utils.helpers import get_current_datetime
from redis_config import CHUNKING_QUEUE_NAME
from processors.chunker import EnhancedChunker
from utils.chat_status_tracker import ChatStatusTracker
from utils.progress_tracker import get_progress_tracker
from utils.content_classifier import ContentType, ContentClassifier
from utils.queue_dispatch import enqueue_worker
from utils.worker_event_loop import start_worker_event_loop

start_worker_event_loop("chunker")

print("✅ Chunker worker loaded with EnhancedChunker")


class SemanticChunker:
    """
    Split text into semantic chunks using heading hierarchy and paragraphs.
    This is the fallback chunker for plain text without structure.
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


def _infer_content_type_from_metadata(metadata: Dict[str, Any]) -> Optional[ContentType]:
    """Infer content type from metadata if available."""
    content_type_str = metadata.get('content_type')
    if content_type_str:
        try:
            return ContentType(content_type_str)
        except ValueError:
            pass

    url = metadata.get('url', '')
    classifier = ContentClassifier()
    if '/docs/' in url or '/documentation/' in url or '/api/' in url:
        return ContentType.DOCUMENTATION
    if '/blog/' in url or '/news/' in url or '/post/' in url:
        return ContentType.ARTICLE
    if '/product/' in url or '/item/' in url or '/p/' in url:
        return ContentType.ECOMMERCE

    return None


# ============================================================
# Chunk-type mapping (new types introduced by EnhancedChunker v2)
# ============================================================
#
# `chunk_type` values emitted by processors/chunker.py:
#   section              -> text
#   table                -> table        (legacy: kept for back-compat)
#   table_summary        -> table        (new: one per table)
#   table_row            -> table_row    (new: one per data row)
#   list                 -> list
#   list_item            -> list_item    (new: one per substantive item)
#   card                 -> mixed
#   product              -> text
#   code                 -> code
#   summary              -> summary      (new: page-level summary)
#   image_description    -> image        (new: contentful images only)
#   media_chunk          -> image        (legacy: kept for back-compat)
#   structured_data      -> text
#   paragraph_group      -> text
#
# NOTE: if `chunks.chunk_type` is a Postgres ENUM, add the new values first:
#   ALTER TYPE chunk_type_enum ADD VALUE IF NOT EXISTS 'table_row';
#   ALTER TYPE chunk_type_enum ADD VALUE IF NOT EXISTS 'list_item';
#   ALTER TYPE chunk_type_enum ADD VALUE IF NOT EXISTS 'summary';
#
CHUNK_TYPE_MAP = {
    # Containers / legacy
    'section': 'text',
    'table': 'table',
    'card': 'mixed',
    'paragraph_group': 'text',
    'product': 'text',
    'code': 'code',
    'list': 'list',
    # New single-fact types
    'table_row': 'table_row',
    'table_summary': 'table',
    'list_item': 'list_item',
    'summary': 'summary',
    # Media
    'image_description': 'image',
    'media_chunk': 'image',
    'structured_data': 'text',
}


@dramatiq.actor(
    actor_name="workers.chunker_worker.chunk_document",
    queue_name=CHUNKING_QUEUE_NAME,
    max_retries=2,
    time_limit=600000
)
def chunk_document(chat_id: str, document_id: str):
    """
    Chunk processed document using EnhancedChunker with structure-aware logic.
    Uses document_structure from metadata for semantic understanding.
    """
    print(f"📦 Chunking document with EnhancedChunker: {document_id} for chat: {chat_id}")

    tracker = get_progress_tracker(chat_id)

    try:
        tracker.update_stage('chunking', 0, "Splitting content into chunks...")

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

        metadata_raw = doc['metadata']
        if isinstance(metadata_raw, str):
            try:
                metadata = json.loads(metadata_raw) if metadata_raw else {}
            except json.JSONDecodeError:
                metadata = {}
        else:
            metadata = metadata_raw or {}

        # ============================================================
        # Resolve canonical page metadata (url, project_id, chat_id)
        # ============================================================
        # The canonical URL and tenant IDs live on `pages`, not on
        # `documents.metadata`. Reading them here once means every chunk
        # inserted below carries the same values the retrieval layer
        # filters on (chunks.chat_id + chunks.source_url). Falls back to
        # metadata if the page row is missing for any reason.
        page_row = execute_one(
            """
            SELECT p.url, p.project_id, p.chat_id
            FROM page_versions pv
            JOIN pages p ON p.id = pv.page_id
            WHERE pv.id = %s
            """,
            (page_version_id,),
        )
        source_url = page_row["url"] if page_row else (
            metadata.get("url") or metadata.get("source_url", "")
        )
        project_id = page_row["project_id"] if page_row else None
        resolved_chat_id = page_row["chat_id"] if page_row else chat_id

        # ============================================================
        # Infer content type for type-aware chunking
        # ============================================================
        content_type = _infer_content_type_from_metadata(metadata)
        if content_type:
            print(f"📋 Inferred content type: {content_type.value}")
        else:
            print("📋 No content type inferred, using generic chunking")

        # ============================================================
        # Skip if this document already has chunks in flight
        # ============================================================
        existing_chunks = execute_query(
            """SELECT id, embedding_status, chunk_index
               FROM chunks
               WHERE document_id = %s
               ORDER BY chunk_index""",
            (document_id,)
        )

        if existing_chunks:
            pending_chunks = [c for c in existing_chunks if c.get('embedding_status') == 'PENDING']
            processing_chunks = [c for c in existing_chunks if c.get('embedding_status') == 'PROCESSING']
            completed_chunks = [c for c in existing_chunks if c.get('embedding_status') == 'COMPLETED']
            failed_chunks = [c for c in existing_chunks if c.get('embedding_status') == 'FAILED']

            print(f"📊 Document {document_id} has {len(existing_chunks)} chunks: "
                  f"{len(completed_chunks)} completed, {len(pending_chunks)} pending, "
                  f"{len(processing_chunks)} processing, {len(failed_chunks)} failed")

            if pending_chunks or processing_chunks:
                print(f"📤 Sending {len(pending_chunks)} pending chunks to embedder")
                tracker.update_stage('embedding', 0, f"Generating embeddings for {len(pending_chunks)} chunks...")
                enqueue_worker(
                    "workers.embedder_worker.embed_chunks",
                    chat_id,
                    document_id,
                )
                return

            if completed_chunks and not pending_chunks and not processing_chunks and not failed_chunks:
                enqueue_worker(
                    "workers.embedder_worker.embed_chunks",
                    chat_id,
                    document_id,
                )
                return

            if failed_chunks and not pending_chunks and not processing_chunks:
                if completed_chunks:
                    tracker.mark_failed(
                        f"{len(failed_chunks)} chunks failed to embed; retry is required"
                    )
                    execute_update(
                        """UPDATE chats
                           SET metadata = jsonb_set(COALESCE(metadata, '{}'::jsonb), '{embedding_failures}', %s)
                           WHERE id = %s""",
                        (json.dumps(len(failed_chunks)), chat_id)
                    )
                else:
                    tracker.mark_failed(f"All {len(failed_chunks)} chunks failed to embed")
                return

        # ============================================================
        # Extract and check structure
        # ============================================================
        document_structure = metadata.get('document_structure', {})

        sections = document_structure.get('sections', [])
        tables = document_structure.get('tables', [])
        lists = document_structure.get('lists', [])
        cards = document_structure.get('cards', [])
        all_text = metadata.get('all_text', '')

        print(f"📊 Structure contains: {len(sections)} sections, {len(tables)} tables, "
              f"{len(lists)} lists, {len(cards)} cards")
        print(f"📊 all_text length: {len(all_text)}")

        has_structure = bool(sections or tables or lists or cards)
        has_fallback_text = bool(all_text and len(all_text.split()) >= 10)

        # ============================================================
        # Choose chunking path
        # ============================================================
        if not document_structure or (not has_structure and not has_fallback_text):
            print("⚠️ No usable content in metadata, falling back to cleaned_content")
            content = doc.get('cleaned_content', '')
            if not content:
                error_msg = "No content available for document"
                print(f"❌ {error_msg}")
                tracker.mark_failed(error_msg)
                return

            chunker = SemanticChunker(chunk_size=500, chunk_overlap=50)
            chunks = chunker.chunk_text(content)
            print(f"📝 Plain text chunker created {len(chunks)} chunks")

        else:
            full_structure = {
                'page_title': metadata.get('page_title') or document_structure.get('page_title', ''),
                'source_url': metadata.get('url') or document_structure.get('source_url', ''),
                'main_content': {
                    'sections': sections,
                    'tables': tables,
                    'lists': lists,
                    'cards': cards,
                    'all_text': all_text,
                    'has_content': has_structure,
                    'product_data': document_structure.get('product_data', {}),
                },
                'structured_data': document_structure.get('structured_data', {}),
                'media_chunks': document_structure.get('media_chunks', []),
                'ui_summary': metadata.get('ui_summary', []),
            }

            url = metadata.get('url', '')
            print(f"📋 Using structured content for: {url}")

            tracker.update_stage('chunking', 30, "Analyzing document structure...")

            chunks = EnhancedChunker.chunk_structure(full_structure, content_type)
            print(f"✨ EnhancedChunker created {len(chunks)} structure-aware chunks")

        tracker.update_stage('chunking', 60, f"Storing {len(chunks)} chunks...")

        # ============================================================
        # Store chunks with ON CONFLICT
        # ============================================================
        now = get_current_datetime().isoformat()
        chunk_ids = []
        skipped_count = 0
        inserted_count = 0
        chunk_type_counts: Dict[str, int] = {}
        ordinal_count = 0

        for chunk_data in chunks:
            chunk_id = str(uuid.uuid4())
            content = chunk_data.get('content', '')
            content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
            chunk_index = chunk_data.get('chunk_index', 0)

            heading_path = json.dumps(chunk_data.get('heading_path', []))
            chunk_type = chunk_data.get('chunk_type', 'text')
            entity_type = chunk_data.get('entity_type', 'content')

            # Map to schema-compatible types
            db_chunk_type = CHUNK_TYPE_MAP.get(chunk_type, 'text')

            chunk_type_counts[db_chunk_type] = chunk_type_counts.get(db_chunk_type, 0) + 1

            # Universal collection signal: 0-based position within a
            # detected collection, or None if this chunk is not part of
            # one. Populated by ContentProcessor._apply_ordinal_map and
            # propagated by EnhancedChunker.chunk_structure.
            ordinal_index = chunk_data.get('ordinal_index')
            if ordinal_index is not None:
                ordinal_count += 1

            result = execute_update(
                """INSERT INTO chunks
                   (id, page_version_id, document_id, chat_id, project_id, source_url,
                    chunk_index, chunk_type,
                    content, context_prefix, heading_path, token_count, chunk_hash,
                    entity_type, chunk_category, section_title, position_in_page, information_density,
                    ordinal_index,
                    embedding_status, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                           %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (page_version_id, chunk_hash) DO NOTHING
                """,
                (
                    chunk_id,
                    page_version_id,
                    document_id,
                    resolved_chat_id,
                    project_id,
                    source_url,
                    chunk_index,
                    db_chunk_type,
                    content,
                    chunk_data.get('context_prefix') or ' > '.join(chunk_data.get('heading_path', [])[:-1]),
                    heading_path,
                    chunk_data.get('token_count', 0),
                    content_hash,
                    entity_type,
                    chunk_data.get('chunk_category', 'main_content'),
                    chunk_data.get('section_title', ''),
                    chunk_data.get('position', 0.0),
                    chunk_data.get('information_density', 1.0),
                    ordinal_index,
                    'PENDING',
                    now,
                    now,
                )
            )

            if result == 0:
                skipped_count += 1
            else:
                inserted_count += 1
                chunk_ids.append(chunk_id)
                try:
                    chunk_file = EnhancedChunker.save_chunk_to_txt(
                        content=content,
                        document_id=document_id,
                        chunk_id=chunk_id,
                        chunk_index=chunk_index,
                        source_url=source_url,
                    )
                    print(f"📝 Saved chunk text: {chunk_file}")
                except OSError as error:
                    print(f"⚠️ Failed to save chunk text for {chunk_id}: {error}")

        print(f"✅ Document {document_id}: inserted {inserted_count} chunks, skipped {skipped_count} duplicates")
        print(f"📊 Chunk type breakdown: {chunk_type_counts}")
        print(f"📊 Chunks with ordinal_index: {ordinal_count} / {len(chunks)}")

        # ============================================================
        # Trigger embedding
        # ============================================================
        if chunk_ids:
            try:
                enqueue_worker(
                    "workers.summary_worker.generate_page_summary",
                    chat_id,
                    document_id,
                )
                print("🧠 Dispatched page summary generation")
            except Exception as e:
                print(f"⚠️ Failed to dispatch summary worker (non-fatal): {e}")

        if chunk_ids:
            tracker.update_stage('embedding', 0, f"Generating embeddings for {len(chunks)} chunks...")
            enqueue_worker(
                "workers.embedder_worker.embed_chunks",
                chat_id,
                document_id,
            )
            print(f"📤 Sent {len(chunk_ids)} chunks to embedder")
        else:
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
                    print(f"📤 Found {len(pending)} pending chunks, sending to embedder")
                    tracker.update_stage('embedding', 0, f"Generating embeddings for {len(pending)} chunks...")
                    enqueue_worker(
                        "workers.embedder_worker.embed_chunks",
                        chat_id,
                        document_id,
                    )
                    return
                elif failed and not pending and not processing:
                    print(f"⚠️ Found {len(failed)} failed chunks; retrying embedding")
                    tracker.update_stage(
                        'embedding',
                        0,
                        f"Retrying {len(failed)} failed embedding chunk(s)...",
                    )
                    enqueue_worker(
                        "workers.embedder_worker.embed_chunks",
                        chat_id,
                        document_id,
                    )
                    return
                elif completed:
                    enqueue_worker(
                        "workers.embedder_worker.embed_chunks",
                        chat_id,
                        document_id,
                    )
                    return

            print(f"⚠️ No chunks found for document {document_id}")
            tracker.mark_failed("No chunks were created for this document")

        return chunk_ids

    except Exception as e:
        error_msg = f"Error chunking document: {str(e)}"
        print(f"❌ {error_msg}")
        import traceback
        traceback.print_exc()

        tracker.mark_failed(error_msg)
        raise


print("✅ Chunker worker registered with EnhancedChunker")
print(f"📋 Listening on queue: {CHUNKING_QUEUE_NAME}")