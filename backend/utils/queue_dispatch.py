import dramatiq

from redis_config import (
    CHUNKING_QUEUE_NAME,
    EMBEDDING_QUEUE_NAME,
    PROCESSING_QUEUE_NAME,
)


_ACTOR_QUEUES = {
    "workers.processor_worker.process_document": PROCESSING_QUEUE_NAME,
    "workers.chunker_worker.chunk_document": CHUNKING_QUEUE_NAME,
    "workers.embedder_worker.embed_chunks": EMBEDDING_QUEUE_NAME,
}


def enqueue_worker(actor_name: str, *args) -> None:
    """Enqueue a worker actor without importing its module into this process."""
    try:
        queue_name = _ACTOR_QUEUES[actor_name]
    except KeyError as exc:
        raise ValueError(f"Unknown worker actor: {actor_name}") from exc

    message = dramatiq.Message(
        queue_name=queue_name,
        actor_name=actor_name,
        args=args,
        kwargs={},
        options={},
    )
    dramatiq.get_broker().enqueue(message)
