# Ingestion Pipeline: From Link Submission to DB Embeddings

This document describes the full ingestion flow for a user-submitted link in this backend. It is meant to help a future agent understand the exact runtime path, the state transitions, and the places where the system can be extended safely.

The pipeline is implemented across:

- API route: api/routes/process.py
- Redis queue setup: redis_client.py and redis_config.py
- Crawl worker: workers/crawler_worker.py
- Fetcher: crawler/fetcher.py
- Crawler orchestration: crawler/crawler.py
- Storage layer: crawler/storage.py
- Processor worker: workers/processor_worker.py
- Chunker worker: workers/chunker_worker.py
- Embedder worker: workers/embedder_worker.py
- Retrieval layer: api/routes/retrieval_pipeline.py

---

## 1. Big Picture

When a user sends a URL through `/api/process-link`, the backend does the following:

1. Validates the request and creates or loads the user/project/chat.
2. Persists a page record for the chat.
3. Enqueues a Redis scraping job.
4. The crawl worker runs asynchronously and fetches the site.
5. Each crawled page is stored as a page version and raw document.
6. A processor worker cleans raw HTML into markdown and extracts media metadata.
7. A chunker worker splits the cleaned content into semantic chunks.
8. An embedder worker generates vector embeddings for each chunk and stores them in PostgreSQL.
9. Later, retrieval code can embed the user question and search the stored chunks.

This is the ingestion path that turns a public website into searchable vector data for RAG.

---

## 2. Entry Point: `/api/process-link`

The API entry point is `process_link` in `api/routes/process.py`.

### What it does

- Accepts a `LinkRequest` payload.
- Validates that content is not empty.
- Detects whether the input is a URL or a natural-language question using `InputDetector`.
- If a URL is detected, validates it with `is_valid_url_for_scraping`.
- Ensures a user exists and a default project exists.
- Ensures there is a chat context for the conversation.
- Rejects invalid first-message behavior:
  - first message in a new chat must be a URL
  - existing chats cannot receive a second URL
- Saves the user message in the `messages` table.
- For a URL, creates a page and enqueues a scraping job.
- For a non-URL question, it may ask retrieval code to answer using previously ingested content.

### Important logic

The critical branch is:

```python
if detection['has_url']:
    page = await PageService.create_page_for_chat(...)
    scraping_job_id = redis_client.add_scraping_job(
        url=url,
        project_id=project_id,
        user_id=user_id,
        chat_id=chat_id,
        message_id=user_message['id']
    )
```

This means the route does not crawl the site directly. It creates the page metadata record and then schedules the scrape asynchronously via Redis.

### Data created immediately

The route creates:

- user (if needed)
- default project (if needed)
- chat (if needed)
- user message
- page row for the URL

The page row is not the scraped content itself; it is the logical association between the chat and the URL.

---

## 3. Redis Queue Job Creation

The job creation is handled by `redis_client.RedisClient.add_scraping_job()` in `redis_client.py`.

### What happens

- Creates a unique `job_id`
- Stores a JSON payload in Redis under a prefixed key like `scraping_job:<job_id>`
- Pushes the same payload onto the configured scraping queue
- Sends a Dramatiq task to `crawl_website`

### Payload structure

The job payload contains:

- `job_id`
- `url`
- `project_id`
- `user_id`
- `chat_id`
- `message_id`
- `page_id`
- `status`
- `created_at`

This is the handoff between the API layer and the crawl worker.

### Important note

The queue is intentionally decoupled from the API request so the HTTP layer returns quickly and the heavy work happens asynchronously.

---

## 4. Crawl Worker: `crawl_website`

The worker is defined in `workers/crawler_worker.py`.

### Trigger

When `add_scraping_job` runs, it also does:

```python
from workers.crawler_worker import crawl_website
crawl_website.send(job_id)
```

This schedules a Dramatiq job against the configured queue (`settings.SCRAPING_QUEUE_NAME`).

### Worker responsibilities

The worker:

- fetches the job data from Redis by `job_id`
- marks the job as `processing`
- runs an async crawler with `Crawler(...)`
- updates the Redis job status to `completed` or `failed`

### Why this is the scrape entry point

This is the first actual crawl stage. It does not parse content itself; it delegates to the crawler classes.

---

## 5. Crawler Orchestration

The main orchestration class is `Crawler` in `crawler/crawler.py`.

### How it works

- Initializes:
  - a URL frontier
  - a Fetcher
  - result counters for pages crawled/discovered/failed
- Loops while there are URLs in the frontier and the page limit has not been reached
- For each URL:
  - calls `fetcher.fetch(url)`
  - if successful, parses the HTML
  - extracts links
  - stores the fetched page and raw HTML in the DB
  - triggers `process_document.send(version['document_id'])`
  - continues traversing additional internal URLs

### The key step

This is the important “handoff” from crawl to processing:

```python
process_document.send(version['document_id'])
```

This means the crawler stores the raw source data first, then sends the document ID to the processing queue, which handles HTML cleanup and conversion.

---

## 6. Fetching the Page

The fetch logic is implemented in `crawler/fetcher.py`.

### Strategy

The fetcher tries a hybrid approach:

- fast HTTPX fetch first
- fall back to Playwright when the system decides that a page likely requires JavaScript rendering
- detect render mode based on heuristics and HTML patterns

### Render decision heuristics

The fetcher uses site strategy detection to decide whether browser rendering is needed. This can be based on:

- domain-level render mode cache
- page URL patterns
- HTML markers like `__next`, `data-reactroot`, script bundles, or JS-heavy patterns

The code also includes Cloudflare detection and browser fallbacks for pages that may challenge automated requests.

### Why this matters

The system is designed to support both:

- static sites fetched by HTTPX
- JS-heavy sites rendered by Playwright

The fetched HTML is then sanitized and stored as raw page content.

---

## 7. Parsing and Storage of Raw HTML

The raw page data is stored by `CrawlerStorage` in `crawler/storage.py`.

### Storage path

For every crawled page, the system creates:

1. a new row in the `pages` table
2. a new row in the `page_versions` table
3. a new row in the `documents` table with raw HTML content

### Page/version/document data model

#### `pages`
Represents the logical page/chat URL association.

#### `page_versions`
Stores the fetched snapshot of a page:

- `status_code`
- `content_type`
- `content_hash`
- `fetch_method` (`httpx` or `playwright`)
- `response_size`
- `fetched_at`

#### `documents`
Stores the raw page content used for downstream processing:

- `page_version_id`
- `content`
- `content_format`
- `metadata`
- `processing_status`

### Important behavior

The storage layer sanitizes HTML before saving it by stripping null bytes and invalid control characters, then hashing the content.

This gives a clean and hashable raw source before the processor worker modifies it.

---

## 8. Processor Worker: Clean HTML into Markdown

The document processing stage is implemented in `workers/processor_worker.py`.

### Trigger

The crawler emits:

```python
process_document.send(version['document_id'])
```

The `process_document` Dramatiq actor then loads the document row and processes it.

### What it does

- Retrieves the raw HTML document plus related page metadata
- Marks the document as `PROCESSING`
- Calls `DocumentProcessor.html_to_markdown(html)`
- Strips script/style/nav/header/footer boilerplate
- Converts HTML to markdown via `markdownify`
- Extracts metadata from HTML tags
- Merges page metadata with extracted metadata
- Saves the cleaned markdown into `cleaned_content`
- Updates document status to `COMPLETED`
- Extracts media assets (images and tables) and saves them in `media_assets`
- Sends a chunking job

### Cleaner behavior

`DocumentProcessor.clean_html()` removes:

- script tags
- style tags
- noscript
- iframe
- header
- footer
- nav
- common boilerplate selectors like `.nav`, `.sidebar`, `.cookie-banner`, `.comments`, etc.

This is the first real “content cleanup” step: turning noisy HTML into more usable text.

### Media handling

The processor also extracts:

- images
- table data
- descriptive metadata for them

These are stored alongside the document and also appended into the markdown as searchable artifact descriptions.

### Important handoff

At the end of processing, it sends the document to the chunker:

```python
from workers.chunker_worker import chunk_document
chunk_document.send(document_id)
```

---

## 9. Chunking Stage

The chunking logic lives in `workers/chunker_worker.py`.

### Why chunking is needed

A website can be very large. We need the retrieval layer to search a much smaller set of relevant text blocks rather than one giant document.

### How chunking works

`SemanticChunker.chunk_text()`:

- removes boilerplate lines
- splits text by markdown headings and paragraphs
- records the heading hierarchy path while chunking
- merges related content under headings
- splits oversize chunks into smaller pieces
- filters out useless fragments, navigation text, and duplicates

### Output

Each chunk created by the chunker is a dictionary with fields like:

- `content`
- `heading_path`
- `heading`
- `chunk_index`
- `token_count`

### DB insertion

The chunker writes rows into the `chunks` table.

Columns include:

- `id`
- `page_version_id`
- `document_id`
- `chunk_index`
- `chunk_type`
- `content`
- `heading_path`
- `token_count`
- `chunk_hash`
- `embedding_status`
- timestamps

### Duplicate protection

It computes a SHA-256 hash of each chunk content and checks:

```python
chunk_exists(page_version_id, content_hash)
```

If the same content already exists for that page version, the chunk is skipped to avoid duplicates.

### Important handoff

After chunk insert, the chunker sends the created chunk IDs to the embedders:

```python
from workers.embedder_worker import embed_chunks
embed_chunks.send(chunk_ids)
```

---

## 10. Embedding Stage

The embedding pipeline is implemented in `workers/embedder_worker.py`.

### What it does

For each chunk that is still `PENDING`:

- updates the chunk status to `PROCESSING`
- sends the chunk content to the embedding model
- stores the resulting embedding in the `chunks` table as a `vector`
- sets `embedding_status = 'COMPLETED'`
- records `embedding_model` and `embedding_dimension`

### Model used

This project currently uses NVIDIA’s OpenAI-compatible embedding API:

- model: `nvidia/llama-nemotron-embed-vl-1b-v2`
- dimension: `2048`

The code wraps the API via the OpenAI Python client:

```python
client = OpenAI(
    api_key=NVIDIA_API_KEY,
    base_url=NVIDIA_BASE_URL
)
```

### Output format

If the API returns a vector shorter than 2048, the code pads it with zeros. If it is longer, it truncates to 2048.

This is important because the retrieval layer later compares vectors via PostgreSQL `pgvector` operators.

### Failure handling

If the API key is missing, it marks all chunks as `FAILED` instead of embedding them. If a single chunk fails during a request, that chunk is updated to `FAILED` and the rest continue.

---

## 11. Database State Flow

The ingestion pipeline moves content through a sequence of database states.

### Pages and versions

A crawl creates rows in these tables:

- `pages`
- `page_versions`
- `documents`

### Document lifecycle

A document begins as raw HTML and then transitions through processing states:

- `PENDING`
- `PROCESSING`
- `COMPLETED`
- `FAILED`

### Chunk lifecycle

A chunk begins as text content and transitions through:

- `PENDING`
- `PROCESSING`
- `COMPLETED`
- `FAILED`

### Retrieval readiness

Only chunks that are both:

- `embedding_status = 'COMPLETED'`
- `embedding IS NOT NULL`

are considered good retrieval candidates.

---

## 12. End-to-End Sequence

This is the runtime path in one compact sequence:

```text
User submits URL
  -> /api/process-link
  -> validate URL and state
  -> create/get user/project/chat
  -> create page record
  -> enqueue Redis scraping job
  -> Redis stores job metadata and pushes queue item
  -> Dramatiq crawl_website(job_id)
  -> Crawler fetches page using Fetcher
  -> HTMLParser extracts metadata and links
  -> CrawlerStorage saves page + page_version + raw document
  -> process_document.send(document_id)
  -> DocumentProcessor cleans HTML to markdown
  -> document processing status goes COMPLETED
  -> chunk_document.send(document_id)
  -> SemanticChunker creates semantic chunks
  -> chunks inserted into DB
  -> embed_chunks.send(chunk_ids)
  -> OpenAI-compatible NVIDIA embedding model generates vectors
  -> chunks updated with embedding and embedding_status=COMPLETED
  -> question-answer retrieval can now search the vectorized content
```

---

## 13. Where the RAG Layer Connects

The ingestion pipeline feeds retrieval later in the stack via the retrieval route in `api/routes/retrieval_pipeline.py`.

The retrieval path is:

1. embed the incoming user question
2. run a vector similarity query against the stored chunk embeddings
3. fetch the closest matching chunks
4. build a prompt from them
5. call the LLM to answer the user

This is the reason the ingestion pipeline is so important: without clean, chunked, and embedded content, the final answer would be low quality or impossible.

---

## 14. Key Files and Their Roles

### API layer

- `api/routes/process.py`
  - accepts input, validates, creates chat context, enqueues scrape

### Redis and queue layer

- `redis_client.py`
  - stores job data and pushes to queue
- `redis_config.py`
  - names queue topics and TTL settings

### Crawl layer

- `crawler/fetcher.py`
  - decides HTTPX vs Playwright strategy
- `crawler/crawler.py`
  - orchestrates a crawl over the site
- `crawler/parser.py`
  - extracts metadata and links
- `crawler/storage.py`
  - inserts page/version/document records

### Worker layer

- `workers/crawler_worker.py`
  - worker that runs the crawl job
- `workers/processor_worker.py`
  - cleans HTML and triggers chunking
- `workers/chunker_worker.py`
  - splits cleaned content into chunks
- `workers/embedder_worker.py`
  - generates vector embeddings for chunks

### Retrieval layer

- `api/routes/retrieval_pipeline.py`
  - retrieves relevant chunks and builds the answer

---

## 15. What an Agent Should Know Before Changing This Pipeline

If you are extending or fixing the ingestion flow, keep these invariants in mind:

### 1. The API route should stay thin
The route is meant to validate and enqueue, not to perform actual scraping logic.

### 2. Scraping is asynchronous by design
The user does not wait for a long crawl to complete. The job is stored in Redis and executed by a worker.

### 3. Crawl, process, chunk, and embed are separate concerns
Each stage has a clear responsibility:

- crawl = fetch raw content
- process = clean and structure content
- chunk = split into retrieval units
- embed = vectorize them

### 4. The DB is the durable source of truth
The ingestion pipeline is not just in memory; each stage persists its progress in PostgreSQL.

### 5. Retrieval quality depends on chunk quality
If the cleaned content is noisy or chunk boundaries are poor, the final answer quality drops.

---

## 16. Known Gaps and Extension Points

This is a useful list for future work:

### For crawl reliability

- add better deduplication across pages
- add domain-level crawl rate limiting
- track crawl depth properly instead of a flat frontier
- handle robots.txt and anti-bot policies more formally
- maintain crawl job status in the database as well as Redis

### For processing quality

- stronger boilerplate detection
- better main-content extraction beyond basic tag stripping
- support for PDFs, images, docs, and non-HTML content
- process metadata more consistently

### For chunk quality

- semantic chunk boundaries by heading tree or sentence similarity
- preserve table/list/code blocks better
- tune chunk size and overlap for the model used downstream

### For embedding quality

- add retry logic with exponential backoff
- support multiple embedding models
- add a per-chunk metadata quality score
- degrade gracefully when the embedding API is unavailable

### For retrieval quality

- filter low-signal chunks before retrieval
- rank by multiple signals beyond cosine similarity
- integrate query rewriting or route-based retrieval

---

## 17. Debugging Tips

When something fails in the ingestion pipeline, these are the first places to check:

### 1. API request path
Check whether the route accepted the request and enqueued a Redis job.

### 2. Redis queue state
Confirm the job exists in Redis and the status changes from `pending` to `processing` to `completed`.

### 3. Database state
Inspect:

- `pages`
- `page_versions`
- `documents`
- `chunks`

Look for missing rows or stuck statuses.

### 4. Worker logs
The workers print messages like:

- `Starting crawl for job: ...`
- `Document {id} processed successfully`
- `Created {n} chunks`
- `Embedded {n} chunks`

These logs are useful to localize the stage where the pipeline gets stuck.

### 5. Missing embeddings
If retrieval returns nothing, the most likely cause is that chunks never reached `embedding_status = 'COMPLETED'` or the embedding API key is unset.

---

## 18. Practical Implementation Summary

The ingestion design in this repo is a standard “queue + worker” pipeline:

- API validates and enqueues
- Redis is the transport layer
- Dramatiq workers execute each processing stage
- PostgreSQL stores durable state
- vectors enable search and retrieval

That architecture is intentionally modular. Each stage can be independently improved without rewriting the rest of the system.

---

## 19. Final Mental Model

Think of ingestion as a pipeline that converts a URL into structured, searchable knowledge:

- URL in -> page metadata tracked
- HTML fetched -> stored as raw snapshot
- HTML cleaned -> markdown document
- markdown segmented -> searchable chunks
- chunks embedded -> vector database

Once that is complete, the system can answer questions grounded in the content that was crawled and embedded.

This is the core of your RAG ingestion workflow.
