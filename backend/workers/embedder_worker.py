# workers/embedder_worker.py
import dramatiq
import json
import os
from typing import List, Optional
from database_sync import execute_query, execute_update, execute_one
from utils.helpers import get_current_datetime
from redis_config import EMBEDDING_QUEUE_NAME
from utils.chat_status_tracker import ChatStatusTracker
from utils.progress_tracker import get_progress_tracker
import logging
from utils.embedding_service import (
    EMBEDDING_DIMENSION,
    EMBEDDING_BATCH_SIZE,
    MODEL_NAME,
    NVIDIA_API_KEY,
    get_embeddings,
)
from utils.worker_event_loop import start_worker_event_loop

start_worker_event_loop("embedder")

# logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.info("✅ Embedder worker loaded")

EMBEDDING_ATTEMPTS_PER_CHUNK = 3
MAX_FAILED_CHUNKS_PER_CHAT = 3

def _mark_document_ready(chat_id: str, document_id: str, message: str) -> bool:
    """Complete a page only after every chunk for it is embedded."""
    counts = execute_one(
        """SELECT COUNT(*) AS total,
                  COUNT(*) FILTER (WHERE embedding_status = 'COMPLETED') AS completed
           FROM chunks WHERE document_id = %s""",
        (document_id,)
    )
    if not counts or not counts.get('total') or counts['total'] != counts['completed']:
        return False

    execute_update(
        """UPDATE crawled_urls
           SET status = 'completed', error_message = NULL, crawled_at = NOW()
           WHERE chat_id = %s AND document_id = %s AND status = 'processing'""",
        (chat_id, document_id)
    )

    url_counts = execute_one(
        """SELECT COUNT(*) AS total,
                  COUNT(*) FILTER (WHERE status = 'completed') AS completed,
                  COUNT(*) FILTER (WHERE status IN ('pending', 'processing')) AS active,
                  COUNT(*) FILTER (WHERE status = 'failed') AS failed
           FROM crawled_urls WHERE chat_id = %s""",
        (chat_id,)
    )
    incomplete = execute_one(
        """SELECT COUNT(*) AS total
           FROM chunks c
           JOIN crawled_urls u ON u.document_id = c.document_id
           WHERE u.chat_id = %s AND c.embedding_status <> 'COMPLETED'""",
        (chat_id,)
    )
    if (
        url_counts
        and url_counts.get('total', 0) > 0
        and url_counts.get('active', 0) == 0
        and url_counts.get('failed', 0) == 0
        and url_counts.get('completed', 0) == url_counts.get('total')
        and incomplete
        and incomplete.get('total', 0) == 0
    ):
        get_progress_tracker(chat_id).mark_completed(message)
        return True
    return False


def _mark_document_failed(chat_id: str, document_id: str, error: str) -> None:
    """Record a document warning without failing the whole chat."""
    execute_update(
        """UPDATE crawled_urls
           SET status = 'failed', error_message = %s, crawled_at = NOW()
           WHERE chat_id = %s AND document_id = %s AND status <> 'completed'""",
        (error[:500], chat_id, document_id)
    )
    tracker = get_progress_tracker(chat_id)
    failed_count = execute_one(
        """SELECT COUNT(*) AS failed
           FROM chunks c
           JOIN crawled_urls u ON u.document_id = c.document_id
           WHERE u.chat_id = %s AND c.embedding_status = 'FAILED'""",
        (chat_id,)
    )
    if failed_count and failed_count.get('failed', 0) >= MAX_FAILED_CHUNKS_PER_CHAT:
        current_chat = execute_one(
            "SELECT status FROM chats WHERE id = %s",
            (chat_id,)
        )
        if not current_chat or current_chat.get('status') != 'failed':
            tracker.mark_failed(
                f"Chat failed after {failed_count['failed']} chunks failed to embed. "
                f"Reason: {error[:300]}"
            )
        return

    tracker.update_stage(
        'embedding',
        90,
        f"Embedding warning: {error[:160]}. Continuing with the remaining chunks.",
    )

    remaining = execute_one(
        """SELECT COUNT(*) AS active
           FROM crawled_urls
           WHERE chat_id = %s AND status IN ('pending', 'processing')""",
        (chat_id,)
    )
    usable_chunks = execute_one(
        """SELECT COUNT(*) AS completed
           FROM chunks c
           JOIN crawled_urls u ON u.document_id = c.document_id
           WHERE u.chat_id = %s AND c.embedding_status = 'COMPLETED'""",
        (chat_id,)
    )
    if (
        remaining
        and remaining.get('active', 0) == 0
        and usable_chunks
        and usable_chunks.get('completed', 0) > 0
    ):
        tracker.mark_completed(
            f"Ready with warning: {error[:140]}"
        )


# workers/embedder_worker.py - Fixed progress calculation

@dramatiq.actor(
    actor_name="workers.embedder_worker.embed_chunks",
    queue_name=EMBEDDING_QUEUE_NAME,
    max_retries=3,
    time_limit=900000
)
def embed_chunks(chat_id: str, document_id: str):
    """
    Generate embeddings for chunks with status tracking.
    """
    if not NVIDIA_API_KEY or NVIDIA_API_KEY == "your-api-key-here":
        error_msg = "NVIDIA_API_KEY not set"
        print(f"❌ {error_msg}")
        # Use PipelineProgressTracker so progress is preserved, not reset to 0
        get_progress_tracker(chat_id).mark_failed(error_msg)
        return 0
    
    print(f"🧠 Embedding chunks for document: {document_id} for chat: {chat_id}")
    
    # Get progress tracker
    tracker = get_progress_tracker(chat_id)
    
    try:
        execute_update(
            """UPDATE crawled_urls
               SET status = 'processing', error_message = NULL
               WHERE chat_id = %s AND document_id = %s AND status = 'failed'""",
            (chat_id, document_id)
        )

        # ✅ Update status: embedding started
        tracker.update_stage('embedding', 0, "Starting embedding generation...")

        # Get all chunks for this document
        chunks = execute_query(
            """SELECT id, content, embedding_status, chunk_index 
               FROM chunks 
               WHERE document_id = %s 
               ORDER BY chunk_index""",
            (document_id,)
        )
        
        if not chunks:
            error_msg = f"No chunks found for document {document_id}"
            print(f"❌ {error_msg}")
            tracker.mark_failed(error_msg)
            return 0
        
        # ✅ Filter only PENDING chunks (not PROCESSING or COMPLETED)
        failed_chunks = [c for c in chunks if c.get('embedding_status') == 'FAILED']
        if failed_chunks:
            execute_update(
                """UPDATE chunks
                   SET embedding_status = 'PENDING', updated_at = %s
                   WHERE document_id = %s AND embedding_status = 'FAILED'""",
                (get_current_datetime().isoformat(), document_id)
            )
            chunks = execute_query(
                """SELECT id, content, embedding_status, chunk_index
                   FROM chunks WHERE document_id = %s ORDER BY chunk_index""",
                (document_id,)
            )

        pending_chunks = [c for c in chunks if c.get('embedding_status') == 'PENDING']
        total_pending = len(pending_chunks)
        total_chunks = len(chunks)
        
        # ✅ Also check for stuck PROCESSING chunks (stale from previous runs)
        processing_chunks = [c for c in chunks if c.get('embedding_status') == 'PROCESSING']
        if processing_chunks:
            print(f"⚠️ Found {len(processing_chunks)} stale PROCESSING chunks, resetting to PENDING")
            for chunk in processing_chunks:
                execute_update(
                    "UPDATE chunks SET embedding_status = 'PENDING', updated_at = %s WHERE id = %s",
                    (get_current_datetime().isoformat(), chunk['id'])
                )
            # Re-query pending chunks
            pending_chunks = execute_query(
                """SELECT id, content, embedding_status, chunk_index 
                   FROM chunks 
                   WHERE document_id = %s AND embedding_status = 'PENDING'
                   ORDER BY chunk_index""",
                (document_id,)
            )
            total_pending = len(pending_chunks)
        
        if total_pending == 0:
            # ✅ Check if all chunks are completed
            completed_chunks = [c for c in chunks if c.get('embedding_status') == 'COMPLETED']
            failed_chunks = [c for c in chunks if c.get('embedding_status') == 'FAILED']
            
            if completed_chunks and not failed_chunks:
                print(f"✅ All {len(completed_chunks)} chunks already embedded")
                _mark_document_ready(
                    chat_id,
                    document_id,
                    f"All {len(completed_chunks)} chunks already embedded",
                )
                return len(completed_chunks)
            elif completed_chunks and failed_chunks:
                error_msg = f"{len(failed_chunks)} chunks failed to embed"
                print(f"❌ {error_msg}")
                _mark_document_failed(chat_id, document_id, error_msg)
                return 0
            elif failed_chunks:
                print(f"❌ All {len(failed_chunks)} chunks failed")
                _mark_document_failed(
                    chat_id,
                    document_id,
                    f"All {len(failed_chunks)} chunks failed to embed",
                )
                return 0
            else:
                print(f"⚠️ No pending chunks found for document {document_id}")
                tracker.mark_failed("No chunks to embed")
                return 0
        
        print(f"📊 Found {total_chunks} total chunks, {total_pending} pending")
        
        # ✅ Update status with chunk count (stage_progress = 10%)
        tracker.update_stage('embedding', 10, f"Embedding {total_pending} chunks...")
        
        chunks_embedded = 0
        chunks_failed = 0
        failed_chunk_errors = []
        
        for batch_start in range(0, total_pending, EMBEDDING_BATCH_SIZE):
            batch = pending_chunks[batch_start:batch_start + EMBEDDING_BATCH_SIZE]
            batch_ids = [chunk['id'] for chunk in batch]
            batch_end = batch_start + len(batch)
            try:
                stage_progress = 10 + int((batch_end / total_pending) * 80)
                tracker.update_stage(
                    'embedding',
                    stage_progress,
                    f"Embedding chunks {batch_end}/{total_pending}..."
                )

                now = get_current_datetime().isoformat()
                placeholders = ','.join(['%s'] * len(batch_ids))
                execute_update(
                    f"UPDATE chunks SET embedding_status = 'PROCESSING', updated_at = %s WHERE id IN ({placeholders})",
                    (now, *batch_ids)
                )
                embeddings = get_embeddings(
                    [chunk['content'] for chunk in batch],
                    max_retries=EMBEDDING_ATTEMPTS_PER_CHUNK,
                )
                for chunk, embedding in zip(batch, embeddings):
                    execute_update(
                        """UPDATE chunks
                           SET embedding = %s::vector, embedding_model = %s,
                               embedding_dimension = %s, embedding_status = 'COMPLETED',
                               updated_at = %s WHERE id = %s""",
                        (embedding, MODEL_NAME, EMBEDDING_DIMENSION, now, chunk['id'])
                    )
                chunks_embedded += len(batch)
                print(f"📊 Embedded {chunks_embedded}/{total_pending} chunks")
            except Exception as e:
                if len(batch) == 1:
                    chunk_id = batch[0]['id']
                    error_msg = f"Error embedding chunk {chunk_id}: {e}"
                    print(f"❌ {error_msg}")
                    execute_update(
                        "UPDATE chunks SET embedding_status = 'FAILED', updated_at = %s WHERE id = %s",
                        (get_current_datetime().isoformat(), chunk_id)
                    )
                    chunks_failed += 1
                    failed_chunk_errors.append(error_msg)
                    continue
                print(f"❌ Error embedding batch {batch_start + 1}-{batch_end}: {e}; retrying individually")
                for chunk in batch:
                    chunk_id = chunk['id']
                    try:
                        embedding = get_embeddings(
                            [chunk['content']],
                            max_retries=EMBEDDING_ATTEMPTS_PER_CHUNK,
                        )[0]
                        execute_update(
                            """UPDATE chunks SET embedding = %s::vector, embedding_model = %s,
                               embedding_dimension = %s, embedding_status = 'COMPLETED',
                               updated_at = %s WHERE id = %s""",
                            (embedding, MODEL_NAME, EMBEDDING_DIMENSION,
                             get_current_datetime().isoformat(), chunk_id)
                        )
                        chunks_embedded += 1
                    except Exception as chunk_error:
                        error_msg = f"Error embedding chunk {chunk_id}: {chunk_error}"
                        print(f"❌ {error_msg}")
                        execute_update(
                            "UPDATE chunks SET embedding_status = 'FAILED', updated_at = %s WHERE id = %s",
                            (get_current_datetime().isoformat(), chunk_id)
                        )
                        chunks_failed += 1
                        failed_chunk_errors.append(error_msg)
        
        # Final status update. A partial document is not usable and cannot
        # complete the chat.
        if chunks_failed > 0 and chunks_embedded == 0:
            print(f"❌ All {total_pending} chunks failed for document {document_id}")
            first_error = failed_chunk_errors[0] if failed_chunk_errors else "Unknown embedding error"
            _mark_document_failed(
                chat_id,
                document_id,
                f"{chunks_failed} chunk(s) failed. Reason: {first_error}",
            )
            return 0
        elif chunks_failed > 0:
            print(f"⚠️ Document {document_id}: {chunks_embedded} embedded, {chunks_failed} failed")
            first_error = failed_chunk_errors[0] if failed_chunk_errors else "Unknown embedding error"
            _mark_document_failed(
                chat_id,
                document_id,
                f"{chunks_failed} of {total_pending} chunks failed. Reason: {first_error}",
            )
            return chunks_embedded
        else:
            print(f"✅ Successfully embedded all {chunks_embedded} chunks for document {document_id}")
            _mark_document_ready(
                chat_id,
                document_id,
                "All chunks embedded successfully",
            )
        
        print(f"🧠 Embedding complete for document {document_id}: {chunks_embedded} embedded, {chunks_failed} failed")
        return chunks_embedded
        
    except Exception as e:
        error_msg = f"Error in embedding process: {str(e)}"
        print(f"❌ {error_msg}")
        import traceback
        traceback.print_exc()
        
        tracker.mark_failed(error_msg)
        raise


print(f"✅ Embedder worker registered with {MODEL_NAME}")
print(f"📋 Listening on queue: {EMBEDDING_QUEUE_NAME}")