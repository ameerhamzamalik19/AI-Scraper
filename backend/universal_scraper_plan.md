# Universal Website Scraping + RAG Platform — V1 Implementation Plan

## 1. Goal

Build a production-oriented platform where a user submits a public website URL, the system crawls it, stores the data, converts pages into clean structured documents, chunks and embeds them, and answers user questions with an LLM grounded in retrieved website content.

Target: approximately 100 users operating in parallel.

V1 excludes authenticated websites, CAPTCHA/bot-protected websites, proxy/captcha bypass, and multimodal/image understanding.

---

## 2. Technology Decisions

| Area | Decision |
|---|---|
| API | FastAPI |
| Queue | Dramatiq |
| Broker | Redis |
| Database | PostgreSQL |
| Vector search | PostgreSQL + pgvector |
| Static fetching | HTTPX |
| JavaScript rendering | Playwright |
| Queue architecture | Separate executable queues/workers |
| Auth websites | Out of scope |
| Bot-protected websites | Out of scope |
| Vector DB | PostgreSQL + pgvector (decision for V1) |
| Chunking | Semantic/hierarchical |
| Target scale | ~100 parallel users |

Celery is intentionally not used. Dramatiq + Redis is the selected queue system.

---

# 3. High-Level Architecture

```text
Client
  |
  v
FastAPI
  |
  +----------------------+
  |                      |
  v                      v
PostgreSQL              Redis
(source of truth)       (Dramatiq broker)
                           |
        +------------------+------------------+----------------+
        |                  |                  |                |
        v                  v                  v                v
   Crawl Queue       Browser Queue      Process Queue     Chunk Queue
        |                  |                  |                |
        v                  v                  v                v
   HTTPX workers      Playwright       Document workers    Chunk workers
                           workers
        |                  |                  |                |
        +------------------+------------------+----------------+
                           |
                           v
                      Embed Queue
                           |
                           v
                    Embedding workers
                           |
                           v
                     PostgreSQL
                      + pgvector
```

The pipeline is intentionally split into independently executable queues. Do not create one giant `scrape_and_process()` task.

---

# 4. Separate Executable Queues

Use these queue/worker groups:

```text
crawl
browser
process
chunk
embed
```

### Crawl queue

Responsible for:

- URL validation after the security layer
- HTTPX fetching
- response analysis
- deciding whether browser rendering is required
- page/version persistence
- URL discovery
- enqueueing browser or processing work

Must not perform chunking or embeddings.

### Browser queue

Responsible for:

- Playwright rendering
- controlled browser/page lifecycle
- collecting final rendered HTML/DOM
- browser-specific retries and timeouts
- enqueueing processing

Browser workers are isolated because browsers are much more resource-intensive than HTTP workers.

### Process queue

Responsible for:

- HTML parsing
- sanitization
- boilerplate detection
- main-content extraction
- Document IR creation
- canonical clean document generation
- extraction validation
- enqueueing chunk work

### Chunk queue

Responsible for:

- heading hierarchy
- semantic block grouping
- token-aware chunking
- table/list/code handling
- chunk metadata
- deterministic chunk hashes
- chunk persistence
- enqueueing embedding work

### Embed queue

Responsible for:

- selecting pending chunks
- batching
- embedding-provider calls
- vector persistence
- model/dimension metadata
- retries and provider rate limits

Each queue can be scaled independently.

---

# 5. Recommended Repository Structure

```text
universal-scraper-rag/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── routes/
│   │   │   └── dependencies.py
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   ├── logging.py
│   │   │   └── security.py
│   │   ├── db/
│   │   │   ├── session.py
│   │   │   └── migrations/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   │   ├── crawl/
│   │   │   ├── browser/
│   │   │   ├── document/
│   │   │   ├── chunking/
│   │   │   ├── embeddings/
│   │   │   └── retrieval/
│   │   └── workers/
│   │       ├── common/
│   │       ├── crawl_worker.py
│   │       ├── browser_worker.py
│   │       ├── process_worker.py
│   │       ├── chunk_worker.py
│   │       └── embed_worker.py
│   ├── tests/
│   ├── pyproject.toml
│   └── Dockerfile
├── docs/
├── docker-compose.yml
├── .env.example
└── README.md
```

Each worker executable should register only the actors belonging to its queue.

---

# 6. Scraper Architecture

```text
Submitted URL
     |
     v
SSRF validation
     |
     v
URL normalization
     |
     v
HTTPX fetch
     |
     v
Response/content analysis
     |
     +---- sufficient ----> persist page
     |
     +---- insufficient --> Browser Queue
                                  |
                                  v
                              Playwright
                                  |
                                  v
                             persist page
                                  |
                                  v
                            Process Queue
```

HTTPX is the default. Playwright is a fallback, not the default for every page.

Browser rendering is expensive and must be resource-limited.

---

# 7. Crawl Controls

Every crawl must have configurable limits:

```text
max_pages
max_depth
max_response_size
max_total_bytes
max_concurrent_requests
max_browser_pages
request_timeout
browser_timeout
per_domain_concurrency
```

Never allow an unbounded crawl.

Normalize URLs before inserting them into the crawl frontier. Resolve relative URLs, normalize scheme/host and default ports, remove fragments, and preserve query parameters when they may identify distinct content.

---

# 8. SSRF Protection

This is mandatory because users supply arbitrary URLs.

Before fetching:

1. Parse and validate the URL.
2. Allow only supported schemes, initially HTTP/HTTPS.
3. Resolve DNS.
4. Reject private, loopback, link-local, reserved and internal destinations.
5. Protect against DNS rebinding.
6. Validate redirect destinations too.
7. Apply response-size and timeout limits.
8. Apply the same restrictions to Playwright navigation.

Reject targets such as localhost, loopback, RFC1918 private ranges, link-local ranges, cloud metadata endpoints, and internal service networks.

Scraped HTML is untrusted input.

---

# 9. Page Storage Model

A logical URL is represented by a `page`; each fetch is a `page_version`.

```text
page
 |
 +-- page_version_1
 +-- page_version_2
 +-- page_version_3
```

Persist raw HTML or a durable raw-content location plus metadata.

Useful page-version fields include:

```text
page_id
url
status_code
content_type
raw_content_location
content_hash
fetch_method
response_size
fetched_at
processing_status
```

Raw HTML and clean content are deliberately separate representations.

For larger production deployments, raw bodies should move to object storage while PostgreSQL stores metadata and the object location.

---

# 10. Processing State

Crawl state and indexing state are distinct.

Recommended lifecycle:

```text
DISCOVERED
  -> FETCHING
  -> FETCHED
  -> PROCESSING
  -> PROCESSED
  -> CHUNKING
  -> CHUNKED
  -> EMBEDDING
  -> INDEXED
```

Failure states:

```text
FETCH_FAILED
PROCESSING_FAILED
CHUNKING_FAILED
EMBEDDING_FAILED
```

A crawl can be complete while its knowledge base is still indexing.

Project-level states can include:

```text
CRAWLING
PROCESSING
CHUNKING
INDEXING
READY
PARTIAL
FAILED
```

---

# 11. Document Processing Pipeline

Do not chunk HTML directly.

Use:

```text
Raw HTML
   |
   v
HTML sanitization
   |
   v
DOM traversal
   |
   v
Boilerplate detection
   |
   v
Main-content extraction
   |
   v
Document IR
   |
   v
Canonical clean document
   |
   v
Validation
   |
   v
Chunk Queue
```

The critical architectural decision is:

```text
Raw HTML != Clean Document != Chunks
```

This allows chunking algorithms to change later without scraping again.

---

# 12. HTML Sanitization

Remove or neutralize clearly non-content/unsafe elements such as:

```text
script
style
noscript
canvas
tracking elements
unsafe embedded content
```

Do not blindly delete `nav`, `header`, `footer`, or `aside`. They are signals of likely boilerplate, not automatic deletion rules.

A footer can contain useful support or product information.

---

# 13. Boilerplate Detection

Use multiple signals:

- semantic tags
- CSS class/id names
- link density
- text density
- DOM position
- repeated content
- content length

Suspicious indicators include:

```text
navbar
navigation
menu
cookie
popup
modal
advert
ads
social
sidebar
```

V1 should use deterministic heuristics.

Cross-page repeated-block detection can be added later to identify site-wide navigation and boilerplate more accurately.

---

# 14. Main-Content Extraction

Score candidate DOM regions using:

```text
text length
paragraph count
heading count
link density
semantic tags
content density
boilerplate indicators
```

Select the strongest content region.

Do not depend exclusively on `<main>` or `<article>` because many websites use inconsistent semantic markup.

---

# 15. Document Intermediate Representation

Do not flatten HTML immediately to plain text.

Create a structured Document IR:

```python
Document:
    metadata
    blocks: list[DocumentBlock]
```

Supported block types should include:

```text
heading
paragraph
list
list_item
table
quote
code
image
link
```

Example:

```json
{
  "type": "heading",
  "level": 2,
  "text": "Authentication",
  "order": 4
}
```

The IR preserves semantic structure and makes chunking deterministic.

---

# 16. Canonical Clean Document

The Document IR should produce a canonical human-readable representation, typically Markdown:

```markdown
# Machine Learning

Machine learning allows computers to learn from data.

## Supervised Learning

Supervised learning uses labeled examples.

- Classification
- Regression
```

Store the clean document independently from raw HTML.

---

# 17. Document Validation

Before chunking, verify:

- meaningful text exists
- extraction is not suspiciously short
- content is not only navigation
- parsing succeeded
- content type is supported
- token/word counts are plausible

Example:

```text
500 KB HTML -> 12 bytes clean text
```

should be flagged rather than silently indexed.

---

# 18. Semantic Chunking

Chunking is its own executable queue.

Pipeline:

```text
Clean Document
     |
     v
Heading hierarchy
     |
     v
Semantic blocks
     |
     v
Compatible block grouping
     |
     v
Token budget
     |
     v
Chunk
     |
     v
Metadata + deterministic hash
```

Primary rule:

> Semantic structure first, token size second.

Never use blind character slicing such as `text[i:i+500]`.

---

# 19. Chunk Size

Start with configurable values around:

```text
target: 500–700 tokens
maximum: ~800 tokens
```

These are starting points for evaluation, not permanent truths.

Use the tokenizer appropriate for the selected embedding model.

---

# 20. Hierarchy Preservation

Each chunk inherits its heading path.

Example:

```json
{
  "heading_path": [
    "Machine Learning",
    "Supervised Learning",
    "Classification"
  ]
}
```

This prevents context loss when a paragraph such as:

```text
Accuracy is...
```

is retrieved without its surrounding headings.

---

# 21. Context Prefix

A chunk can have a deterministic context prefix:

```text
Document: Acme Documentation
Section: Authentication
Subsection: API Keys
```

Store it separately from `content`.

Embedding input can be:

```text
context_prefix + "

" + content
```

while the canonical source content remains unchanged.

Do not rely on blind fixed token overlap as the primary mechanism. Hierarchy/context inheritance is preferred.

---

# 22. Special Chunk Types

Use semantic chunk types such as:

```text
text
table
list
code
quote
mixed
```

### Tables

Preserve headers and row/column relationships.

If a table must be split, repeat the header in every table chunk.

### Lists

Keep related list items together where possible. If split, preserve the parent heading and ordering.

### Code

Keep code blocks intact when possible and store the language.

### Images

V1 does not require vision processing. Preserve useful metadata such as alt text, caption, title, surrounding context and source URL.

---

# 23. Chunk Metadata

Each chunk should include at least:

```text
id
page_version_id
chunk_index
chunk_type
content
context_prefix
heading_path
token_count
chunk_hash
embedding_status
embedding_model
embedding_dimension
created_at
updated_at
```

Source URL/title should be obtainable through the page/page-version relationship.

---

# 24. Deterministic Chunk Hashing

Calculate a stable hash over normalized:

```text
chunk content
+
heading path
+
chunk type
```

For example:

```text
SHA-256(normalized_chunk_payload)
```

If a recrawl produces the same hash, reuse the existing embedding.

This enables efficient incremental indexing.

---

# 25. Embedding Architecture

Hide the provider behind an interface:

```python
class EmbeddingProvider(Protocol):
    async def embed(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        ...
```

Do not spread provider-specific SDK calls across the application.

The embedding implementation must support batching.

Never make one network request per chunk unless the provider requires it.

---

# 26. Embedding Queue

```text
pending chunks
    |
    v
Embed Queue
    |
    v
embedding workers
    |
    v
batch provider request
    |
    v
vectors
    |
    v
PostgreSQL + pgvector
```

Workers must respect provider rate limits.

Embedding failures should retry independently from scraping/processing failures.

---

# 27. PostgreSQL + pgvector

Use PostgreSQL as both relational database and vector store.

Conceptual `chunks` table:

```text
chunks
------
id UUID PK
page_version_id UUID FK
chunk_index INT
chunk_type TEXT
content TEXT
context_prefix TEXT
heading_path JSONB
token_count INT
chunk_hash TEXT
embedding VECTOR(N)
embedding_model TEXT
embedding_dimension INT
embedding_status TEXT
created_at TIMESTAMP
updated_at TIMESTAMP
```

`N` depends on the selected embedding model.

Do not introduce a separate vector database in V1. PostgreSQL + pgvector is the agreed vector-storage and vector-search solution.

---

# 28. Vector Store Abstraction

Although V1 uses PostgreSQL + pgvector, keep vector operations behind an internal `VectorStore` interface.

Conceptually:

```python
class VectorStore(Protocol):
    async def upsert(self, vectors: list[VectorRecord]) -> None:
        ...

    async def search(
        self,
        query_vector: list[float],
        filters: SearchFilters,
        top_k: int,
    ) -> list[SearchResult]:
        ...

    async def delete(self, chunk_ids: list[str]) -> None:
        ...
```

The V1 implementation is:

```text
PostgresVectorStore
        |
        v
PostgreSQL + pgvector
```

This keeps vector-specific operations isolated without introducing a second database. A dedicated vector database may be evaluated later only if measured scale/workload justifies it.

---

# 28. Vector Index

HNSW is the initial candidate.

Before production, benchmark:

- retrieval recall
- latency
- index size
- insert/update performance

IVFFlat remains an alternative if benchmark results justify it.

---

# 29. Incremental Reprocessing

The complete incremental flow is:

```text
Recrawl
  |
  v
Page content hash
  |
  +-- unchanged --> reuse existing processing
  |
  +-- changed ----> reprocess
                       |
                       v
                    rechunk
                       |
                       v
                 chunk hash compare
                       |
                       +-- unchanged --> reuse embedding
                       |
                       +-- changed ----> embed
```

This prevents expensive full re-indexing.

---

# 30. Retrieval + QA

Once indexing is ready:

```text
User question
    |
    v
FastAPI
    |
    v
Query embedding
    |
    v
pgvector similarity search
    |
    v
Project/knowledge-base filter
    |
    v
Top-K chunks
    |
    v
Optional reranking later
    |
    v
LLM
    |
    v
Grounded answer + sources
```

V1 retrieval should start with vector similarity plus metadata filtering.

Later options:

- hybrid BM25 + vector
- reranking
- query expansion
- multi-query retrieval
- parent-document retrieval

Do not build these prematurely.

---

# 31. Source Attribution

Every retrieved chunk must retain:

```text
source URL
page title
heading path
chunk content
```

The final answer should expose source references.

This improves user trust and makes retrieval failures debuggable.

---

# 32. Database Entities

Core entities:

```text
users
projects
crawl_jobs
pages
page_versions
documents
chunks
```

Supporting entities may include:

```text
crawl_urls / URL frontier
processing_events
worker_failures
```

Relationship:

```text
User
  -> Projects
      -> Crawl Jobs
          -> Pages
              -> Page Versions
                  -> Document
                      -> Chunks
                          -> Embedding
```

All pipeline stages must use foreign keys and uniqueness constraints to prevent duplicates.

---

# 33. Idempotency

Every queue task must be safe to retry.

Examples:

```text
process_page(page_version_id)
chunk_page(page_version_id)
embed_chunks(chunk_ids)
```

Running them twice must not produce duplicate documents/chunks/vectors.

Use unique constraints, deterministic IDs/hashes where appropriate, and transactional upserts.

---

# 34. Retry Policy

Retry transient errors:

- network timeout
- temporary HTTP failure
- Redis issue
- browser crash
- embedding provider temporary failure
- rate limiting

Do not endlessly retry permanent errors:

- invalid URL
- SSRF rejection
- unsupported scheme
- authenticated page
- permanently forbidden resource

Use bounded exponential backoff and explicit failure/dead-letter states.

---

# 35. Redis

Redis is used for:

- Dramatiq broker
- queueing
- transient coordination
- short-lived locks/counters where needed

Redis is not the source of truth for durable application state.

PostgreSQL remains authoritative.

---

# 36. FastAPI Responsibilities

FastAPI should:

- validate requests
- create database records
- enqueue tasks
- return IDs/status
- expose crawl status
- expose QA endpoints
- stream/report progress if required

FastAPI must not perform long-running crawling/indexing directly inside request handlers.

---

# 37. 100-User Capacity

100 parallel users does not mean 100 unlimited browsers.

Start with controlled concurrency, for example:

```text
HTTP crawl concurrency: 20–50
Browser concurrency: 4–10
Document processing: 10–20
Chunk processing: 10–20
Embedding: provider-dependent
```

These are starting values and must be load-tested.

Excess work must queue instead of spawning unlimited processes or browsers.

Browser workers are the most aggressively resource-limited component.

---

# 38. Observability

Track at minimum:

### Crawl

- pages discovered
- pages fetched
- HTTP status distribution
- fetch latency
- bytes downloaded
- browser fallback rate

### Processing

- extraction duration
- extraction quality
- clean-document size
- processing failures

### Chunking

- chunks per page
- token distribution
- oversized chunks
- chunking failures

### Embedding

- batch size
- provider latency
- failures
- rate limits
- throughput

### Infrastructure

- queue depth
- worker utilization
- DB latency
- Redis latency
- API latency
- memory usage

Use structured logs with:

```text
user_id
project_id
crawl_job_id
page_id
page_version_id
chunk_id
task_id
```

---

# 39. Security

Mandatory:

- SSRF protection
- URL validation
- redirect validation
- response-size limits
- browser navigation restrictions
- timeouts
- resource limits
- authentication/authorization
- project-level data isolation
- safe handling of untrusted scraped HTML
- no arbitrary code execution from page content

---

# 40. Development Phases

## Phase 0 — Foundation

Build:

- repository
- Python environment
- FastAPI
- PostgreSQL
- Redis
- Dramatiq
- Docker Compose
- configuration
- migrations
- structured logging
- test infrastructure

Deliverable:

```text
FastAPI + PostgreSQL + Redis + one working Dramatiq queue
```

## Phase 1 — Crawl Core

Implement:

- URL validation
- SSRF protection
- URL normalization
- crawl jobs
- URL frontier
- HTTPX fetcher
- page/page-version persistence
- crawl budgets
- retries

Deliverable:

```text
URL -> website crawl -> stored pages
```

## Phase 2 — Browser Fallback

Implement:

- browser queue
- Playwright worker
- resource limits
- JS detection/fallback
- browser timeout/retry handling

Deliverable:

```text
static -> HTTPX
JS-required -> Playwright
```

## Phase 3 — Document Processing

Implement:

- HTML sanitization
- DOM parsing
- boilerplate heuristics
- main-content extraction
- Document IR
- Markdown generation
- validation

Deliverable:

```text
HTML -> clean structured document
```

## Phase 4 — Chunking

Implement:

- heading hierarchy
- semantic block handling
- tokenizer integration
- semantic recursive chunking
- tables
- lists
- code
- metadata
- deterministic hashes

Deliverable:

```text
clean document -> high-quality chunks
```

## Phase 5 — Embeddings

Implement:

- EmbeddingProvider
- concrete provider
- batching
- embedding queue
- retries
- model/dimension tracking
- pgvector
- HNSW benchmark

Deliverable:

```text
chunks -> vectors -> searchable knowledge base
```

## Phase 6 — Retrieval

Implement:

- query embedding
- pgvector search
- project filtering
- top-K retrieval
- source metadata
- retrieval-debug endpoint

Deliverable:

```text
question -> relevant chunks
```

## Phase 7 — LLM QA

Implement:

- prompt construction
- context injection
- grounded answer generation
- source citations
- insufficient-information behavior
- context-size management

Deliverable:

```text
question -> grounded answer + sources
```

## Phase 8 — Production Hardening

Implement:

- load testing
- queue backpressure
- DB pool tuning
- Redis tuning
- browser memory limits
- metrics
- alerts
- recovery
- dead-letter handling
- security testing
- crawl-quality benchmarks
- retrieval evaluation

Target:

```text
~100 concurrent users
```

---

# 41. Testing Strategy

Build a test corpus containing:

- static pages
- JS-rendered pages
- poor semantic HTML
- navigation-heavy sites
- tables
- lists
- code documentation
- long articles
- empty pages
- redirects
- large pages
- duplicate URLs
- query-parameter URLs
- malformed HTML
- misleading navigation

Measure:

```text
fetch success
main-content precision/recall
chunk quality
retrieval recall
answer grounding
latency
CPU/RAM usage
queue throughput
```

Create a golden QA dataset:

```text
website
question
expected relevant page
expected relevant section
expected answer facts
```

Use it to evaluate changes to extraction, chunking, embedding and retrieval.

---

# 42. AI Agent Engineering Rules

Any coding agent working on this repository must follow these rules:

1. Never collapse separate worker queues into one generic worker.
2. Never perform long-running work in FastAPI request handlers.
3. Never bypass SSRF validation.
4. Do not implement authenticated/bot-protected scraping in V1.
5. Never embed raw HTML.
6. Never chunk raw HTML.
7. Preserve document hierarchy.
8. Preserve tables, lists and code as semantic structures.
9. Make queue tasks idempotent.
10. Use database constraints to prevent duplicate processing.
11. Keep embedding-provider SDK calls behind the provider interface.
12. Do not add a separate vector DB in V1; PostgreSQL + pgvector is the agreed vector store.
13. Never silently discard failed processing.
14. Never allow unlimited browser contexts/pages.
15. Keep concurrency values configurable.
16. Keep secrets in environment/configuration, never source code.
17. Add tests for meaningful behavior.
18. Prefer composable services over giant classes/functions.
19. Keep raw HTML, clean documents and chunks as separate artifacts.
20. Preserve provenance through every pipeline stage.

---

# 43. Definition of Done

V1 is complete when:

- A user can submit a public URL.
- The system crawls within configured limits.
- Static pages use HTTPX.
- JS-required pages can use Playwright.
- SSRF protection is active.
- Pages and versions are persisted.
- HTML becomes a clean structured document.
- Boilerplate is reasonably removed.
- Heading hierarchy is preserved.
- Tables/lists/code are handled correctly.
- Documents are semantically chunked.
- Chunk hashes support incremental indexing.
- Embeddings are generated through a dedicated queue.
- Vectors are stored in pgvector.
- Retrieval is project-scoped.
- An LLM answers using retrieved context.
- Answers expose sources.
- Crawl, browser, processing, chunking and embedding have separate executable worker queues.
- Queue failures are retryable and observable.
- The system survives a meaningful 100-user load test without uncontrolled resource growth.

---

# 44. Final Architecture Principle

The system is intentionally divided into independently scalable stages:

```text
ACQUISITION
    |
    v
Raw HTML
    |
    v
UNDERSTANDING
    |
    v
Document IR
    |
    v
Clean Document
    |
    v
INDEXING
    |
    v
Semantic Chunks
    |
    v
EMBEDDING
    |
    v
Vectors
    |
    v
RETRIEVAL
    |
    v
Context
    |
    v
GENERATION
    |
    v
LLM Answer + Sources
```

Each stage has a single responsibility, its own executable queue, its own failure/retry semantics, and its own scaling characteristics.

## Immediate implementation order

Start here and do not jump ahead:

1. Repository + Docker Compose.
2. PostgreSQL + migrations.
3. Redis + Dramatiq.
4. Separate worker entry points.
5. Crawl database schema.
6. URL validation + SSRF protection.
7. HTTPX crawl queue.
8. Playwright browser queue.
9. Document processing queue.
10. Document IR.
11. Chunk queue.
12. Embedding queue + pgvector.
13. Retrieval.
14. LLM QA.
15. Load testing and hardening.

The chunking pipeline is now part of the main architecture, while the design deliberately keeps the raw HTML, clean document, and chunk representations independent so each stage can evolve without forcing a re-scrape.
