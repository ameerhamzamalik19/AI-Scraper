# Progress Bar Workflow

This document describes how the ingestion workers update the chat `status`, `progress`, and `current_step` fields. The source of truth for pipeline progress is `utils/progress_tracker.py`; Redis job status and database chat status are separate concepts.

## Status and percentage model

`PipelineProgressTracker` maps each pipeline stage to an overall percentage band:

| Stage | Overall range | Typical status text |
| --- | ---: | --- |
| `pending` | 0% | Initializing... |
| `crawling` | 5-30% | Starting crawl / found pages |
| `processing` | 30-60% | Extracting and cleaning HTML |
| `chunking` | 60-80% | Analyzing structure / storing chunks |
| `embedding` | 80-100% | Generating embeddings |
| `completed` | 100% | Processing complete |
| `failed` | Last known percentage | Error message is stored separately |

The percentage passed by a worker is a percentage *inside the current stage*, not the final chat percentage. The tracker converts it with:

```text
overall = stage_min + (stage_progress / 100) * (stage_max - stage_min)
```

The result is truncated to an integer. For example, `processing` at 50% becomes 45% overall, and `embedding` at 90% becomes 98% overall.

Every successful tracker update writes `status`, `progress`, `updated_at`, and, when supplied, `current_step` to `chats`. Completion also writes `completed_at`. It then attempts a WebSocket broadcast. A broadcast failure is non-fatal.

The tracker has two monotonicity checks:

1. The in-memory tracker rejects a backwards stage transition.
2. The database check rejects a backwards stage transition when the current database status is known, and prevents progress from decreasing within the same stage.

The database check is important because crawler, processor, chunker, and embedder workers can run in different processes and each can create or retrieve a separate in-memory tracker.

## End-to-end workflow

### 1. Request initialization

For a new URL request, `api/routes/process.py`:

1. Creates the chat and calls `ChatStatusTracker.initialize`, setting `pending` and `0%`.
2. Creates the page record.
3. Sets `crawling` with stage progress `0`, which maps to `5%` overall.
4. Enqueues the Redis scraping job.

If job creation fails, the route calls `mark_failed` and keeps the last known percentage. For a non-URL question, the route uses `processing` at `0` and later calls `mark_completed("Answer generated!")`, which sets the status to `completed` through the progress tracker even though `ChatStatusTracker` also defines an `answered` status.

### 2. Crawler worker

`workers/crawler_worker.py:crawl_website`:

1. Sets the Redis scraping-job status to `processing`.
2. Sets the chat to `crawling` with stage progress `5`, or approximately `6%` overall.
3. Runs the BFS same-domain crawler.
4. After the crawl, updates the chat to `crawling` with stage progress `50`, which is `17%` overall, and reports discovered and crawled page counts in `current_step`.
5. Sets the Redis scraping-job status to `completed`.
6. Counts successful crawled pages and writes that value to `chats.pending_documents`.
7. Moves the chat to `processing` at stage progress `0`, which is `30%` overall.

`crawler/crawler.py` dispatches `process_document(chat_id, document_id)` immediately after each page version is stored. Failed fetches do not create a processor job.

The crawler does not update the progress bar for every page. Its progress is a coarse crawl-stage signal, not a page completion percentage.

### 3. Processor worker

`workers/processor_worker.py:process_document`:

1. Sets `processing` at stage progress `0` (`30%` overall).
2. Loads the document and sets stage progress `20` (`36%`) while cleaning and extracting content.
3. Sets document `processing_status` to `PROCESSING`.
4. Extracts document structure, metadata, images, and tables.
5. Sets stage progress `50` (`45%`) during media extraction.
6. Stores cleaned content, metadata, and media assets, then sets the document to `COMPLETED`.
7. Sets `chunking` at stage progress `0` (`60%` overall).
8. Enqueues `chunk_document(chat_id, document_id)`.

If processing fails, the document is marked `FAILED` and the chat is marked failed while retaining the last known progress where possible.

### 4. Chunker worker

`workers/chunker_worker.py:chunk_document`:

1. Sets `chunking` at stage progress `0` (`60%`).
2. If existing chunks are found, it either sends pending chunks to the embedder, completes immediately if all are already embedded, or records a partial/all-chunk failure.
3. For a new chunk set, stage progress `30` maps to `66%` while the document structure is analyzed.
4. Stage progress `60` maps to `72%` while chunks are stored.
5. If chunks were inserted, it sets `embedding` at stage progress `0` (`80%`) and enqueues `embed_chunks`.
6. If no new chunks were inserted, it checks existing chunk states and either retries embedding, completes, or fails.

The chunker does not use the helper `mark_chunking`; it calls `update_stage` directly.

### 5. Embedder worker

`workers/embedder_worker.py:embed_chunks`:

1. If the NVIDIA API key is unavailable, it immediately marks the chat failed.
2. Otherwise, it sets `embedding` at stage progress `0` (`80%`).
3. It loads all chunks, resets stale `PROCESSING` chunks to `PENDING`, and determines the pending count.
4. If there are no pending chunks, it either completes, records partial failure as completed, or fails.
5. For pending work, it sets stage progress `10` (`82%`).
6. For each chunk, it calculates stage progress from `10` to `90`. The resulting overall percentage runs from about `82%` through `98%`.
7. It marks each chunk `PROCESSING`, generates and stores the vector, then marks the chunk `COMPLETED`. Per-chunk errors mark that chunk `FAILED` and processing continues.
8. After the document finishes, it decrements `chats.pending_documents` atomically.
9. Only the worker that observes the counter reach `0` calls `mark_completed`, setting the chat to `completed` at `100%`.

Partial embedding failure is currently treated as a completed pipeline with an `embedding_failures` count in chat metadata. If every chunk for the final document fails, the worker attempts to change the result to `failed`.

## Failure behavior

`PipelineProgressTracker.mark_failed` tries to preserve the tracker or database progress and writes `status = 'failed'`, `error_message`, and the preserved percentage. It does not set `completed_at`. A completed or answered chat is protected from later tracker updates, including failure updates.

The fallback `ChatStatusTracker.mark_failed` is different: it writes `progress = 0`. Therefore, if the direct failure update itself raises an exception, the displayed percentage can reset to zero.

## Bugs and risks found

### 1. `pending_documents` has a race with processor and embedder jobs

The crawler dispatches processor jobs inside `Crawler._crawl_page`, before `crawl_website` returns from the crawl and sets `pending_documents`. A fast processor/chunker/embedder chain can decrement the default counter of `0`, observe `0`, and mark the chat completed before the crawler writes the real page count. The later crawler update is blocked because completed chats are terminal, leaving a completed chat with a stale positive `pending_documents` value.

The counter should be initialized before any processor job can run, or completion should use a database query/transaction that derives remaining documents from actual document states.

### 2. Multiple page pipelines race through one chat-level stage

Each page has an independent processor, chunker, and embedder job, but `status` and `progress` are stored once per chat. One page can advance the chat to `chunking` or `embedding` while another page is still processing. The database guard then rejects the other page's backward update. This makes the displayed stage represent whichever page advances furthest, rather than aggregate completion across all pages.

The `pending_documents` counter only gates final completion; it does not aggregate processing, chunking, or embedding percentages.

### 3. The final all-failed check only examines the current document

When the last document finishes with all chunks failed, the embedder checks for completed chunks using `WHERE documents.id = %s` for the current document. If another document succeeded, this current-document-only check can still mark the whole chat failed. The decision should inspect all documents/chunks belonging to the chat.

### 4. Failure updates are not fully monotonic or atomic

`mark_failed` performs its terminal-state read and update separately and does not use the same database stage/progress guard as `_update_chat_status`. Concurrent failure/completion updates can race. It also does not broadcast its direct database update, so clients may not receive a WebSocket failure event unless the fallback path is used.

### 5. A completed document can leave the chat in processing

The processor updates the tracker to `processing` before checking whether the document is already `COMPLETED`, then returns without advancing or enqueueing chunking. If a duplicate processor job runs after a chat has not otherwise advanced, it can leave the chat at a processing percentage with no follow-up job.

### 6. Forward stage jumps are allowed

The tracker rejects backward transitions but allows a worker to jump over stages. For example, a chunker or embedder can set `embedding` even if processing for other pages is incomplete. This is partly intentional for concurrent page jobs, but it means the status is not a strict representation of aggregate pipeline order.

### 7. Redis job status and chat status can disagree

The crawler marks the Redis job `completed` before all processor, chunker, and embedder jobs finish. Redis `completed` means crawling finished; chat `completed` is intended to mean ingestion and embedding finished. Consumers must not treat the Redis job status as overall pipeline readiness.

### 8. Dead or misleading tracker code

The helper methods `mark_crawling`, `mark_processing`, `mark_chunking`, and `mark_embedding` are not used by the workers; workers mostly call `update_stage` directly. `STAGES[*]['weight']`, `_stage_progress`, and some imports are unused. There is also a duplicated terminal-state comment in the tracker. These do not directly break progress, but they make the implementation harder to audit.

## Expected successful percentage sequence

For a typical single-page crawl, the database may receive approximately:

```text
pending 0
crawling 6 -> 17
processing 30 -> 36 -> 45
chunking 60 -> 66 -> 72
embedding 80 -> 82 -> ... -> 98
completed 100
```

The exact sequence is asynchronous and may skip updates. With multiple pages, updates from different workers can interleave, and database guards may reject stale/backward writes.