import os
import time
from typing import List, Sequence

import httpx
from openai import OpenAI
from openai import APIStatusError


NVIDIA_API_KEY = os.getenv("EMBEDDING_MODEL_API_KEY", "")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
MODEL_NAME = "nvidia/llama-nemotron-embed-vl-1b-v2"
EMBEDDING_DIMENSION = 2048
_HTTP_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
EMBEDDING_BATCH_SIZE = max(1, int(os.getenv("EMBEDDING_BATCH_SIZE", "1")))
MAX_INPUT_CHARS = 24000
_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=NVIDIA_API_KEY, base_url=NVIDIA_BASE_URL, timeout=_HTTP_TIMEOUT)
    return _client


def _normalize_embedding(embedding: List[float]) -> List[float]:
    if len(embedding) >= EMBEDDING_DIMENSION:
        return embedding[:EMBEDDING_DIMENSION]
    return embedding + [0.0] * (EMBEDDING_DIMENSION - len(embedding))


def get_embeddings(texts: Sequence[str], max_retries: int = 3) -> List[List[float]]:
    """Generate embeddings for multiple texts in one provider request."""
    if not texts:
        return []

    normalized_texts = []
    empty_embedding = [0.0] * EMBEDDING_DIMENSION
    for text in texts:
        value = text or ""
        if len(value) > MAX_INPUT_CHARS:
            value = value[:MAX_INPUT_CHARS]
        normalized_texts.append(value)

    if not NVIDIA_API_KEY or NVIDIA_API_KEY == "your-api-key-here":
        return [[0.0] * EMBEDDING_DIMENSION for _ in normalized_texts]

    results = [empty_embedding if not text.strip() else None for text in normalized_texts]
    request_indexes = [index for index, result in enumerate(results) if result is None]
    if not request_indexes:
        return results

    client = _get_client()
    for attempt in range(max_retries):
        try:
            response = client.embeddings.create(
                input=[normalized_texts[index] for index in request_indexes],
                model=MODEL_NAME,
                encoding_format="float",
                extra_body={
                    "modality": ["text"],
                    "input_type": "query",
                    "truncate": "NONE",
                },
                timeout=_HTTP_TIMEOUT,
            )
            ordered = sorted(response.data, key=lambda item: item.index)
            if len(ordered) != len(request_indexes):
                raise RuntimeError("Embedding provider returned an incomplete batch")
            for index, item in zip(request_indexes, ordered):
                results[index] = _normalize_embedding(item.embedding)
            return results
        except APIStatusError as error:
            if error.status_code < 500:
                response_body = getattr(error, "response", None)
                response_body = response_body.text[:500] if response_body is not None else str(error)
                raise RuntimeError(
                    f"Embedding provider rejected request (HTTP {error.status_code}, "
                    f"items={len(request_indexes)}, max_chars={max(len(normalized_texts[index]) for index in request_indexes)}): "
                    f"{response_body}"
                ) from error
            if attempt >= max_retries - 1:
                raise
            time.sleep(2 ** attempt)
        except Exception:
            if attempt >= max_retries - 1:
                raise
            time.sleep(2 ** attempt)

    raise RuntimeError("Embedding generation failed")

def get_embedding(text: str, max_retries: int = 3) -> List[float]:
    """Generate a text embedding without importing the Dramatiq embedder actor."""
    return get_embeddings([text], max_retries=max_retries)[0]
