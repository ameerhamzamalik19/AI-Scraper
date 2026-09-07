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

# logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.info("✅ Embedder worker loaded")

# NVIDIA NIM API Configuration - Using OpenAI-compatible endpoint
NVIDIA_API_KEY = os.getenv("EMBEDDING_MODEL_API_KEY", "")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
MODEL_NAME = "nvidia/llama-nemotron-embed-vl-1b-v2"
# MODEL_NAME = "nomic-embed-text"
EMBEDDING_DIMENSION = 2048

# Try to import OpenAI
try:
    from openai import OpenAI
    print("✅ OpenAI client imported successfully")
except ImportError:
    print("❌ OpenAI not installed. Run: pip install openai")
    raise


def get_embedding(text: str, max_retries: int = 3) -> List[float]:
    """
    Generate embedding using NVIDIA Llama-Nemotron-Embed-VL-1b-v2 via OpenAI-compatible API.
    Includes retry logic and token truncation.
    """
    if not text or not text.strip():
        print("⚠️ Empty text, returning zero embedding")
        return [0.0] * EMBEDDING_DIMENSION
    
    if not NVIDIA_API_KEY or NVIDIA_API_KEY == "your-api-key-here":
        print("⚠️ NVIDIA_API_KEY not set. Using zero embedding.")
        return [0.0] * EMBEDDING_DIMENSION
    
    # Estimate token count (rough: 1 token ≈ 4 characters)
    estimated_tokens = len(text) // 4
    max_tokens = 8192
    
    # Truncate if needed
    if estimated_tokens > max_tokens:
        max_chars = max_tokens * 4
        text = text[:max_chars]
        print(f"⚠️ Truncated text to {max_chars} chars (~{max_tokens} tokens)")
    
    client = OpenAI(
        api_key=NVIDIA_API_KEY,
        base_url=NVIDIA_BASE_URL
    )
    
    for attempt in range(max_retries):
        try:
            print(f"📤 Sending embedding request (text length: {len(text)} chars, attempt {attempt + 1})")
            
            response = client.embeddings.create(
                input=[text],
                model=MODEL_NAME,
                encoding_format="float",
                extra_body={
                    "modality": ["text"],
                    "input_type": "query",
                    "truncate": "NONE"
                }
            )
            
            embedding = response.data[0].embedding
            
            print(f"📥 Received embedding with {len(embedding)} dimensions")
            
            # Ensure correct dimension
            if len(embedding) == EMBEDDING_DIMENSION:
                return embedding
            elif len(embedding) > EMBEDDING_DIMENSION:
                return embedding[:EMBEDDING_DIMENSION]
            else:
                return embedding + [0.0] * (EMBEDDING_DIMENSION - len(embedding))
            
        except Exception as e:
            error_str = str(e)
            print(f"❌ Embedding attempt {attempt + 1} failed: {error_str}")
            
            # Check if it's a token limit error
            if "Input length" in error_str and "exceeds maximum allowed token size" in error_str:
                # Try with shorter text
                max_chars = 4096 * 4  # 4096 tokens
                text = text[:max_chars]
                print(f"⚠️ Token limit exceeded, truncating to {max_chars} chars")
                continue
            
            if attempt < max_retries - 1:
                import time
                wait_time = 2 ** attempt  # Exponential backoff: 1, 2, 4 seconds
                print(f"⏳ Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                print(f"❌ All {max_retries} attempts failed")
                raise
    
    # Fallback
    print("⚠️ Returning zero embedding as fallback")
    return [0.0] * EMBEDDING_DIMENSION


# workers/embedder_worker.py - Fixed progress calculation

@dramatiq.actor(
    queue_name=EMBEDDING_QUEUE_NAME,
    max_retries=3,
    time_limit=300000
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
                tracker.mark_completed(f"All {len(completed_chunks)} chunks already embedded")
                return len(completed_chunks)
            elif completed_chunks and failed_chunks:
                print(f"⚠️ {len(completed_chunks)} embedded, {len(failed_chunks)} failed")
                tracker.mark_completed(f"Embedded {len(completed_chunks)} chunks, {len(failed_chunks)} failed")
                return len(completed_chunks)
            elif failed_chunks:
                print(f"❌ All {len(failed_chunks)} chunks failed")
                tracker.mark_failed(f"All {len(failed_chunks)} chunks failed to embed")
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
        
        for i, chunk in enumerate(pending_chunks):
            chunk_id = chunk['id']
            chunk_index = chunk.get('chunk_index', i)
            
            try:
                # ✅ Calculate stage progress as percentage of chunks processed
                # stage_progress goes from 10 to 90 (leaving room for completion)
                stage_progress = 10 + int(((i + 1) / total_pending) * 80)
                
                # ✅ Update status every chunk (not every 5)
                tracker.update_stage(
                    'embedding', 
                    stage_progress, 
                    f"Embedding chunk {i+1}/{total_pending}..."
                )
                
                # Update chunk status to processing
                now = get_current_datetime().isoformat()
                execute_update(
                    "UPDATE chunks SET embedding_status = 'PROCESSING', updated_at = %s WHERE id = %s",
                    (now, chunk_id)
                )
                
                # Generate embedding
                embedding = get_embedding(chunk['content'])
                
                # Store embedding
                execute_update(
                    """UPDATE chunks 
                       SET embedding = %s::vector,
                           embedding_model = %s,
                           embedding_dimension = %s,
                           embedding_status = 'COMPLETED',
                           updated_at = %s
                       WHERE id = %s""",
                    (embedding, MODEL_NAME, EMBEDDING_DIMENSION, now, chunk_id)
                )
                
                chunks_embedded += 1
                
                if chunks_embedded % 10 == 0:
                    print(f"📊 Embedded {chunks_embedded}/{total_pending} chunks")
                
            except Exception as e:
                error_msg = f"Error embedding chunk {chunk_id}: {str(e)}"
                print(f"❌ {error_msg}")
                now = get_current_datetime().isoformat()
                execute_update(
                    "UPDATE chunks SET embedding_status = 'FAILED', updated_at = %s WHERE id = %s",
                    (now, chunk_id)
                )
                chunks_failed += 1
        
        # ✅ Final status update with proper partial failure handling
                # ─── Decrement the shared counter and only mark_completed when ALL pages are done ───
        def _decrement_and_maybe_complete(success_msg: str, failure_count: int = 0):
            """
            Atomically decrement pending_documents. Returns the new value so
            this worker knows whether it is the last one to finish.
            """
            from database_sync import execute_one as _eo, execute_update as _eu

            row = _eo(
                """UPDATE chats
                      SET pending_documents = pending_documents - 1
                    WHERE id = %s
                RETURNING pending_documents""",
                (chat_id,)
            )
            remaining = row.get('pending_documents') if row else None
            logger.info(
                f"📊 Chat {chat_id}: pending_documents now {remaining} "
                f"after document {document_id} finished"
            )

            if remaining == 0:
                # This is the last document — safe to mark complete
                tracker.mark_completed(success_msg)
            elif remaining is not None and remaining < 0:
                # Counter went negative — the increment in _crawl_page()
                # did not run before this embedder finished. Log loudly;
                # do NOT complete, because the page count is unknown.
                logger.error(
                    f"Chat {chat_id}: pending_documents is {remaining} (negative). "
                    f"The per-page increment in _crawl_page() likely did not execute "
                    f"before this embedder job ran. Check crawler/crawler.py Fix 2."
                )
            # else: other documents still in flight, leave status as-is

        # Final status update
        if chunks_failed > 0 and chunks_embedded == 0:
            # All chunks failed for this document
            print(f"❌ All {total_pending} chunks failed for document {document_id}")
            _decrement_and_maybe_complete(
                f"Pipeline complete (document {document_id}: all chunks failed)",
                failure_count=chunks_failed
            )
            # If this was the last document and everything failed, mark as failed instead
            from database_sync import execute_one as _eo2
            row2 = _eo2("SELECT pending_documents FROM chats WHERE id = %s", (chat_id,))
            if (row2 and row2.get('pending_documents') == 0):
                # Check if any chunks at all completed across all documents
                from database_sync import execute_one as _eo3
                total_done = _eo3(
                    """SELECT COUNT(*) AS n FROM chunks
                         JOIN documents ON documents.id = chunks.document_id
                        WHERE documents.id = %s
                          AND chunks.embedding_status = 'COMPLETED'""",
                    (document_id,)
                )
                if not total_done or total_done.get('n', 0) == 0:
                    tracker.mark_failed(f"All {total_pending} chunks failed to embed")
        elif chunks_failed > 0:
            # Partial failure — still usable, complete gracefully with a note (Bug #6 fix)
            print(f"⚠️ Document {document_id}: {chunks_embedded} embedded, {chunks_failed} failed")
            _decrement_and_maybe_complete(
                f"Pipeline complete ({chunks_embedded} chunks embedded, {chunks_failed} failed)",
                failure_count=chunks_failed
            )
        else:
            # Full success for this document
            print(f"✅ Successfully embedded all {chunks_embedded} chunks for document {document_id}")
            _decrement_and_maybe_complete(
                f"All chunks embedded successfully"
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