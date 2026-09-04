# workers/embedder_worker.py
import dramatiq
import json
import os
from typing import List, Optional
from database_sync import execute_query, execute_update, execute_one
from utils.helpers import get_current_datetime
from redis_config import EMBEDDING_QUEUE_NAME
from utils.chat_status_tracker import ChatStatusTracker

print("✅ Embedder worker loaded")

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
        ChatStatusTracker.mark_failed(chat_id, error_msg)
        return 0
    
    print(f"🧠 Embedding chunks for document: {document_id} for chat: {chat_id}")
    
    try:
        # Update status: embedding started
        ChatStatusTracker.update(
            chat_id,
            status=ChatStatusTracker.STATUS_EMBEDDING,
            progress=80,
            current_step="Starting embedding generation..."
        )

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
            ChatStatusTracker.mark_failed(chat_id, error_msg)
            return 0
        
        # Filter only PENDING chunks
        pending_chunks = [c for c in chunks if c.get('embedding_status') == 'PENDING']
        total_pending = len(pending_chunks)
        total_chunks = len(chunks)
        
        if total_pending == 0:
            print(f"⚠️ No pending chunks for document {document_id}. All {total_chunks} chunks already processed.")
            ChatStatusTracker.update(
                chat_id,
                status=ChatStatusTracker.STATUS_COMPLETED,
                progress=100,
                current_step=f"All {total_chunks} chunks already embedded"
            )
            return total_chunks
        
        print(f"📊 Found {total_chunks} total chunks, {total_pending} pending")
        
        # Update status with chunk count
        ChatStatusTracker.update(
            chat_id,
            status=ChatStatusTracker.STATUS_EMBEDDING,
            progress=82,
            current_step=f"Embedding {total_pending} chunks..."
        )
        
        chunks_embedded = 0
        chunks_failed = 0
        
        for i, chunk in enumerate(pending_chunks):
            chunk_id = chunk['id']
            chunk_index = chunk.get('chunk_index', i)
            
            try:
                # Update status periodically
                if i % 5 == 0:
                    progress = 82 + int((i / total_pending) * 14)  # 82% to 96%
                    ChatStatusTracker.update(
                        chat_id,
                        status=ChatStatusTracker.STATUS_EMBEDDING,
                        progress=progress,
                        current_step=f"Embedding chunk {i+1}/{total_pending}..."
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
        
        # Final status update
        if chunks_failed > 0:
            ChatStatusTracker.update(
                chat_id,
                status=ChatStatusTracker.STATUS_COMPLETED,
                progress=100,
                current_step=f"Embedded {chunks_embedded} chunks, {chunks_failed} failed"
            )
            print(f"⚠️ Completed with errors: {chunks_embedded} embedded, {chunks_failed} failed")
        else:
            ChatStatusTracker.update(
                chat_id,
                status=ChatStatusTracker.STATUS_COMPLETED,
                progress=100,
                current_step=f"All {chunks_embedded} chunks embedded!"
            )
            print(f"✅ Successfully embedded all {chunks_embedded} chunks")
        
        print(f"🧠 Embedding complete for document {document_id}: {chunks_embedded} embedded, {chunks_failed} failed")
        return chunks_embedded
        
    except Exception as e:
        error_msg = f"Error in embedding process: {str(e)}"
        print(f"❌ {error_msg}")
        import traceback
        traceback.print_exc()
        
        ChatStatusTracker.mark_failed(chat_id, error_msg)
        raise


print(f"✅ Embedder worker registered with {MODEL_NAME}")
print(f"📋 Listening on queue: {EMBEDDING_QUEUE_NAME}")