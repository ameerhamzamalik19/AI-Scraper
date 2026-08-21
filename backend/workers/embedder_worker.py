# workers/embedder_worker.py
import dramatiq
import json
import os
from typing import List, Optional
from database_sync import execute_query, execute_update, execute_one
from utils.helpers import get_current_datetime
from redis_config import EMBEDDING_QUEUE_NAME

print("✅ Embedder worker loaded")

# NVIDIA NIM API Configuration - Using OpenAI-compatible endpoint
NVIDIA_API_KEY = os.getenv("EMBEDDING_MODEL_API_KEY", "")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
MODEL_NAME = "nvidia/llama-nemotron-embed-vl-1b-v2"
EMBEDDING_DIMENSION = 2048

# Try to import OpenAI
try:
    from openai import OpenAI
    print("✅ OpenAI client imported successfully")
except ImportError:
    print("❌ OpenAI not installed. Run: pip install openai")
    raise


def get_embedding(text: str) -> List[float]:
    """
    Generate embedding using NVIDIA Llama-Nemotron-Embed-VL-1B-v2 via OpenAI-compatible API.
    """
    if not text or not text.strip():
        return [0.0] * EMBEDDING_DIMENSION
    
    if not NVIDIA_API_KEY or NVIDIA_API_KEY == "your-api-key-here":
        print("⚠️ NVIDIA_API_KEY not set. Using zero embedding.")
        return [0.0] * EMBEDDING_DIMENSION
    
    try:
        client = OpenAI(
            api_key=NVIDIA_API_KEY,
            base_url=NVIDIA_BASE_URL
        )
        
        print(f"📤 Sending embedding request (text length: {len(text)} chars)")
        
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
        print(f"❌ Error generating embedding: {e}")
        print(f"❌ Error details: {type(e)}")
        if hasattr(e, 'response'):
            print(f"❌ Response: {e.response}")
        raise


@dramatiq.actor(
    queue_name=EMBEDDING_QUEUE_NAME,
    max_retries=3,
    time_limit=300000
)
def embed_chunks(chunk_ids: List[str]):
    """
    Generate embeddings for chunks (SYNC version).
    """
    if not NVIDIA_API_KEY or NVIDIA_API_KEY == "your-api-key-here":
        print("❌ NVIDIA_API_KEY not set. Marking chunks as FAILED.")
        now = get_current_datetime().isoformat()
        for chunk_id in chunk_ids:
            execute_update(
                "UPDATE chunks SET embedding_status = 'FAILED', updated_at = %s WHERE id = %s",
                (now, chunk_id)
            )
        print(f"❌ Marked {len(chunk_ids)} chunks as FAILED due to missing API key")
        return 0
    
    print(f"🧠 Embedding {len(chunk_ids)} chunks with {MODEL_NAME}")
    
    chunks_embedded = 0
    
    for chunk_id in chunk_ids:
        try:
            # Get chunk
            chunk = execute_one(
                "SELECT id, content, embedding_status FROM chunks WHERE id = %s",
                (chunk_id,)
            )
            
            if not chunk:
                print(f"❌ Chunk {chunk_id} not found")
                continue
            
            if chunk.get('embedding_status') != 'PENDING':
                print(f"⚠️ Chunk {chunk_id} already processed (status: {chunk.get('embedding_status')})")
                continue
            
            # Update to processing
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
            
            if chunks_embedded % 5 == 0:
                print(f"📊 Embedded {chunks_embedded} chunks")
            
        except Exception as e:
            print(f"❌ Error embedding chunk {chunk_id}: {e}")
            now = get_current_datetime().isoformat()
            execute_update(
                "UPDATE chunks SET embedding_status = 'FAILED', updated_at = %s WHERE id = %s",
                (now, chunk_id)
            )
    
    print(f"✅ Embedded {chunks_embedded} chunks")
    return chunks_embedded


print(f"✅ Embedder worker registered with {MODEL_NAME}")
print(f"📋 Listening on queue: {EMBEDDING_QUEUE_NAME}")