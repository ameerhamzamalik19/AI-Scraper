import os
import time
from typing import List

from openai import OpenAI


NVIDIA_API_KEY = os.getenv("EMBEDDING_MODEL_API_KEY", "")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
MODEL_NAME = "nvidia/llama-nemotron-embed-vl-1b-v2"
EMBEDDING_DIMENSION = 2048


def get_embedding(text: str, max_retries: int = 3) -> List[float]:
    """Generate a text embedding without importing the Dramatiq embedder actor."""
    if not text or not text.strip():
        return [0.0] * EMBEDDING_DIMENSION

    if not NVIDIA_API_KEY or NVIDIA_API_KEY == "your-api-key-here":
        return [0.0] * EMBEDDING_DIMENSION

    estimated_tokens = len(text) // 4
    if estimated_tokens > 8192:
        text = text[:8192 * 4]

    client = OpenAI(api_key=NVIDIA_API_KEY, base_url=NVIDIA_BASE_URL)
    for attempt in range(max_retries):
        try:
            response = client.embeddings.create(
                input=[text],
                model=MODEL_NAME,
                encoding_format="float",
                extra_body={
                    "modality": ["text"],
                    "input_type": "query",
                    "truncate": "NONE",
                },
            )
            embedding = response.data[0].embedding
            if len(embedding) >= EMBEDDING_DIMENSION:
                return embedding[:EMBEDDING_DIMENSION]
            return embedding + [0.0] * (EMBEDDING_DIMENSION - len(embedding))
        except Exception:
            if attempt >= max_retries - 1:
                raise
            time.sleep(2 ** attempt)

    raise RuntimeError("Embedding generation failed")
