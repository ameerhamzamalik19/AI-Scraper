# Ingestion Pipeline

## Scope

This document describes the implemented URL-ingestion path in the backend, from an HTTP request reaching FastAPI through crawling, persistence, content processing, chunking, and embedding. It also records the technologies, strategies, configuration, and verified implementation risks.

The system is a queue-driven website-to-RAG ingestion pipeline:

```text
HTTP URL request
  -> FastAPI validation and chat/project setup
  -> PostgreSQL page record
  -> Redis job metadata + Dramatiq crawl actor
  -> BFS same-domain crawler
  -> HTTPX / Playwright / optional Bright Data fetch
  -> page version + raw document in PostgreSQL
  -> processing actor
  -> cleaned text, structure, media, metadata
  -> chunking actor
  -> PostgreSQL chunks
  -> embedding actor
  -> vector embeddings and searchable corpus
```

## 1. Application startup

`main.py` creates the FastAPI application and registers the chat, process, scraping, user, and WebSocket routers. The application starts with Uvicorn on port `8000` when run directly.

During the FastAPI lifespan:

1. The running asyncio event loop is captured for thread-safe WebSocket/Redis publishing.
2. The WebSocket connection manager initializes Redis.
3. On shutdown, the database pool is closed and WebSocket connections are closed.

The service uses CORS configured by `CORS_ORIGINS`. The root and health endpoints only report service health; they do not perform ingestion.

Main technologies at this layer:

- Python and FastAPI
- Uvicorn
- Asyncio
- Redis for status/pub-sub support
- PostgreSQL through async and synchronous database helpers
- WebSockets for live progress updates

## 2. URL request enters the backend

The URL enters through `POST /api/process-link`, implemented in `api/routes/process.py`. The request is represented by `LinkRequest` and the response by `ProcessLinkResponse`.

The endpoint performs these steps:

1. Rejects empty input.
2. Gets or creates a user with `UserService`.
3. Gets or creates the default project with `ProjectService`.
4. Uses `InputDetector.detect_input_type()` to determine whether the content contains a URL.
5. Validates a detected URL with `is_valid_url_for_scraping()`.
6. Gets the supplied chat or creates a new chat with `ChatService`.
7. Initializes the in-memory progress tracker for a new chat.
8. Enforces the conversation rule that the first message must contain a URL.
9. Rejects a second URL in an existing conversation.
10. Loads recent chat history and persists the user message with `MessageService`.
11. Broadcasts the user message through the WebSocket manager.

A URL is therefore associated with a user, project, chat, and user message before crawling starts.

### URL-specific setup

For a URL message, the endpoint:

1. Creates a page row through `PageService.create_page_for_chat()`.
2. Changes progress to the `crawling` stage.
3. Publishes the status to connected WebSocket clients.
4. Calls `redis_client.add_scraping_job()` with the URL, project ID, user ID, chat ID, and message ID.
5. Returns an assistant message saying that the URL was received and queued.

The HTTP request does not wait for the website to be crawled. The API response only confirms setup and queueing. The assistant response is saved as a normal chat message and returned to the client.

If Redis is unavailable or job creation fails, the endpoint marks the progress tracker as failed and returns a warning message rather than raising a queue-specific error.

### Non-URL input

A non-URL message in an existing chat follows the retrieval path instead of ingestion. `answer_user_question()` is run in a worker thread, and the answer is stored and broadcast. A new chat cannot begin with a question.

## 3. Queue and worker dispatch

`RedisClient.add_scraping_job()` creates a UUID job ID and stores JSON job metadata under:

```text
scraping_job:<job_id>
```

The metadata contains the URL, project ID, user ID, chat ID, message ID, status, and creation timestamp. The key uses a configured TTL.

The method also:

- Pushes serialized job data into the Redis `scraping_queue` list.
- Sends the job ID to the Dramatiq `crawl_website` actor.

The crawler actor is configured with:

- Queue: `SCRAPING_QUEUE_NAME`, normally `scraping_queue`
- Maximum retries: 3
- Time limit: 600,000 ms

The worker reads the job metadata from Redis, marks it `processing`, creates a `Crawler`, runs its async crawl with `asyncio.run()`, and finally closes the worker's database pool.

The distributed execution model is:

- FastAPI handles request validation and immediate response.
- Redis stores job state and provides messaging infrastructure.
- Dramatiq executes crawl, processing, chunking, and embedding jobs.
- PostgreSQL is the durable source of ingestion data.

## 4. Crawl orchestration

`crawler/crawler.py` owns the crawl. A `Crawler` is initialized with the URL, project, user, chat, page, and maximum page count. The configured default is `MAX_PAGES_PER_CRAWL = 5`.

The crawler creates:

- A `URLFrontier`
- A hybrid `Fetcher`
- An in-memory list of crawled URL records
- Result counters for crawled, discovered, and failed pages

At the start, the seed URL is recorded as `pending` in the `crawled_urls` table and in memory.

The crawl loop continues while the frontier has a URL and the visited count is below the page limit:

1. Pop the next URL from the frontier.
2. Wait `REQUEST_DELAY` seconds between requests after the first page.
3. Fetch the URL.
4. Parse metadata and internal links.
5. Persist the page and page version.
6. Create or persist the raw document.
7. Increment the chat's pending-document counter.
8. Dispatch `process_document` for the document.
9. Mark the URL visited.
10. Add discovered links to the frontier.
11. Record the URL as completed and broadcast progress.

Failed fetches and exceptions are recorded as failed URL records and increment `pages_failed`. The crawler continues to other queued URLs when possible.

At the end it closes the fetcher, publishes final crawl progress, and returns crawl statistics to the crawler worker. The worker stores those statistics in Redis and publishes a crawl summary over Redis pub/sub/WebSockets.

### Frontier strategy

`URLFrontier` implements breadth-first search (BFS):

- The seed URL is depth 0.
- URLs are held in a FIFO list.
- A visited set prevents repeated crawls.
- A queued set prevents duplicate queue entries.
- Only links with the same exact `scheme://netloc` are accepted.
- Fragments and trailing path slashes are removed for deduplication.
- Common image, media, document, archive, feed, CSS, and JavaScript extensions are skipped.
- A page contributes at most 100 links to the crawler, even though the parser can return up to 500.

The configuration contains `MAX_CRAWL_DEPTH = 3`, but the current crawler does not enforce it. The crawl is limited by page count instead.

### URL parsing strategy

`HTMLParser` uses BeautifulSoup with the built-in `html.parser` backend. It:

- Resolves relative links with `urljoin()`.
- Drops fragments.
- Rejects `javascript:` and `mailto:` links.
- Keeps same-domain links.
- Extracts title, description, keywords, and canonical URL metadata.
- Can remove scripts, styles, noscript, iframe, header, footer, and navigation tags for basic text extraction.

The crawler currently uses metadata and links, while full content cleanup is deferred to the processor worker.

## 5. Fetching strategies

`crawler/fetcher.py` uses a hybrid strategy:

```text
HTTPX fast request
  -> if blocked, thin, failed, or JavaScript-dependent:
Playwright browser rendering
  -> if still unsuccessful and configured:
Bright Data Web Unlocker
  -> rotate HTTP user agent and retry HTTPX
```

### HTTPX

HTTPX is the preferred first request method because it is fast and lightweight. The async client uses:

- Followed redirects, up to five redirects
- HTTP/2
- A 30-second request timeout by default
- Rotating browser-like user-agent values
- Browser-style accept and fetch headers
- Gzip/deflate/Brotli response handling where available
- Charset detection from Content-Type and HTML meta tags
- UTF-8, then Latin-1, decoding fallbacks
- HTML cleanup for null bytes and invalid control characters

The fetcher rejects or escalates responses that are not successful HTML responses.

### Blocking, login, and JavaScript detection

The fetcher detects:

- HTTP 401 and 403
- Cloudflare challenge indicators
- CAPTCHA and reCAPTCHA markup
- Rate limiting and retry headers
- Amazon WAF and CloudFront challenge signals
- Common access-denied and bot-detection phrases
- Login/authentication paths and page messages
- Empty SPA roots such as `__next`, `__nuxt`, `root`, and `app`
- Empty tables/lists that suggest AJAX loading
- Thin pages containing multiple fetch/XHR/Axios patterns

A first page with useful visible text is cached as an HTTPX site strategy. A JavaScript shell or thin page selects Playwright for the site. The site strategy is cached by scheme and network location for the lifetime of the `Fetcher` instance.

### Playwright

Playwright is used for JavaScript-rendered or blocked pages. The implementation:

- Runs Chromium headlessly in a separate spawned process.
- Keeps the browser alive during a crawl.
- Uses a fresh browser context for each URL.
- Waits for `networkidle`.
- Waits for a body with more than 100 characters when possible.
- Adds a two-second safety wait.
- Applies `playwright-stealth` when installed.
- Masks several automation indicators such as `navigator.webdriver`.
- Uses a 60-second browser timeout by default.
- Closes the browser process after the crawl.

The fetcher accepts a force-Playwright domain set, but the current set is empty.

### Bright Data Web Unlocker

Bright Data is optional and enabled only when both `BRIGHTDATA_API_KEY` and `BRIGHTDATA_ZONE_NAME` are configured. It is attempted after Playwright fails. The request uses:

- Bright Data's `/request` endpoint
- A configured zone
- Raw response format
- Rendering enabled
- Optional country targeting

The fetcher records that Bright Data has been tried per site and can switch the site strategy to Playwright after a Bright Data failure.

### Politeness and limitations

The crawler adds a one-second delay between page requests, but it does not currently show a robots.txt fetch or robots policy enforcement. The crawler also uses a fixed maximum page count and does not persist its in-memory frontier as a durable crawl frontier.

## 6. Persistence after fetching

For a successful page, `CrawlerStorage` performs two main database operations.

### Page

`get_or_create_page()` currently always creates a new page UUID. It stores:

- Project ID
- Chat ID
- Original URL
- Normalized URL value supplied by the caller
- Creation and update timestamps

Despite its name, it does not currently look up and reuse an existing page.

### Page version and raw document

`create_page_version()`:

1. Sanitizes HTML by removing null bytes and invalid control characters.
2. Calculates a SHA-256 content hash.
3. Inserts a `page_versions` row with status code, content type, fetch method, response size, and timestamps.
4. Inserts a `documents` row containing the raw HTML, initially with `content_format = 'html'` and `processing_status = 'PENDING'`.
5. Returns both the page-version ID and document ID.

The document is the handoff point to the processing pipeline.

## 7. Document processing

`workers/processor_worker.py` registers `process_document` on `processing_queue`. It has two retries and a 600,000 ms time limit.

For each document, the worker:

1. Loads raw HTML, document metadata, processing status, and source page URL.
2. Marks the progress stage as `processing`.
3. Skips duplicate processing if the document is already completed and forwards it to chunking.
4. Marks the document `PROCESSING`.
5. Extracts page metadata with `DocumentProcessor.extract_metadata_from_html()`.
6. Runs `ContentProcessor.process_html()` to create structured content.
7. Extracts images and tables with media heuristics.
8. Classifies the page using URL patterns and `ContentClassifier`.
9. Adds media descriptions and selected structured media data to searchable text.
10. Merges raw metadata, extracted metadata, structure, source URL, content type, and analysis statistics.
11. Stores cleaned/searchable text in `documents.cleaned_content`.
12. Stores merged JSON metadata and marks the document `COMPLETED`.
13. Stores media rows in `media_assets`.
14. Dispatches `chunk_document(chat_id, document_id)`.

### Content extraction strategies

The processor preserves more than plain text. Its output can include:

- Page title and source URL
- Heading and section hierarchy
- Paragraphs
- Lists
- Tables, headers, rows, and summaries
- Image descriptions
- Visible image text and extracted entities
- Content-type metadata
- UI/document structure

### Media analysis

Media analysis is deliberately selective:

- Ollama vision is disabled when `OLLAMA_API_KEY` is absent.
- Small images and likely icons/decorations are skipped.
- Images without alt text or surrounding context are commonly skipped.
- Larger, contextual images and likely charts/screenshots may be analyzed.
- Cleanly parseable tables are converted without an LLM.
- Complex tables may be sent to Ollama vision/text analysis.
- The default configured vision model is `gemma4:31b-cloud` through the Ollama cloud endpoint.

This reduces external model calls and preserves useful visual information for retrieval.

## 8. Chunking

`workers/chunker_worker.py` registers `chunk_document` on `chunking_queue`. It prefers `EnhancedChunker` from `processors/chunker.py` and has a plain-text `SemanticChunker` fallback.

### Structure-aware path

When structured metadata is usable, the worker builds a structure containing:

- Page title
- Source URL
- Sections
- Tables
- Lists
- All extracted text
- Product data where present
- UI summary data

`EnhancedChunker` creates chunks that preserve:

- Heading paths
- Section boundaries
- Tables
- Lists
- Product details
- Content type and entity type
- Source URL and page title
- Token/word counts
- Chunk hashes
- Content structure flags
- Relevance and quality scores

Content types include documentation, article, ecommerce, data table, and card/listing-style content. Entity and category metadata can later boost retrieval ranking.

### Fallback path

If structure is missing or does not contain enough text, `SemanticChunker`:

- Removes common standalone footer/navigation lines.
- Splits around headings and paragraphs.
- Uses a nominal 500-word chunk size with 50-word overlap for long text.
- Adds heading paths and token counts.
- Removes short, low-value, duplicate, or boilerplate chunks.

### Chunk persistence

Each chunk is inserted into PostgreSQL with:

- Page version and document IDs
- Chunk index and type
- Text content
- Heading path
- Token count
- SHA-256 content hash
- Entity type and section metadata
- Information-density fields
- `embedding_status = 'PENDING'`

The database uses `ON CONFLICT (document_id, chunk_index) DO NOTHING` to avoid duplicate indexes on repeated chunk jobs.

## 9. Embedding generation

`workers/embedder_worker.py` registers `embed_chunks` on `embedding_queue`. It uses the OpenAI Python client against NVIDIA's OpenAI-compatible API:

- Base URL: `https://integrate.api.nvidia.com/v1`
- Model: `nvidia/llama-nemotron-embed-vl-1b-v2`
- Expected dimension: 2048
- API key environment variable: `EMBEDDING_MODEL_API_KEY`

For each pending chunk, the worker:

1. Marks the chunk `PROCESSING`.
2. Estimates tokens from character count and truncates oversized input.
3. Calls the embeddings endpoint with text modality and query input type.
4. Retries up to three times with exponential delays.
5. Pads or truncates the returned vector to 2048 values.
6. Stores the vector, model, dimension, and `COMPLETED` status.
7. Marks individual failures as `FAILED`.
8. Decrements the chat's `pending_documents` counter after the document finishes.
9. Marks the chat complete when the counter reaches zero.

If the embedding key is missing, the worker marks the overall progress as failed and returns without embedding. The `get_embedding()` helper itself has a zero-vector fallback, but the actor rejects missing credentials before it reaches that fallback.

## 10. Post-ingestion retrieval readiness

After successful embedding, retrieval in `api/routes/retrieval_pipeline.py` can use the chat's corpus:

1. A follow-up question may be rewritten into a standalone question by an Ollama model while preserving named entities.
2. The question is embedded using the same embedding helper.
3. Vector search uses pgvector distance and filters by chat and completed embedding status.
4. PostgreSQL full-text search uses `content_tsv` and English `tsquery`.
5. Vector and keyword results are fused using Reciprocal Rank Fusion (RRF).
6. Category and entity boosts adjust ranking.
7. Confidence thresholds reject weak or ambiguous retrieval results.
8. The selected chunk text is supplied to an Ollama generation model with a prompt that prohibits unsupported external knowledge.

Ingestion is complete only when the processed document has useful chunks and the chunks have usable embeddings. A raw page or processed document alone is not sufficient for semantic retrieval.

## 11. Technologies and strategies summary

### Core services

- FastAPI and Uvicorn for HTTP API hosting
- PostgreSQL for users, projects, chats, pages, versions, documents, media, chunks, and embeddings
- pgvector/halfvec for vector similarity search
- Redis for job metadata, queues, status, and pub/sub
- Dramatiq for background actors and retries
- WebSockets for crawl and pipeline progress

### Crawling and parsing

- HTTPX async client for fast fetching
- Playwright Chromium for JavaScript rendering
- `playwright-stealth` and browser fingerprint masking
- Optional Bright Data Web Unlocker for difficult sites
- BeautifulSoup for HTML parsing
- BFS same-domain crawling
- URL normalization and duplicate filtering
- User-agent rotation and request delay
- Cloudflare, WAF, CAPTCHA, login, and SPA detection
- Charset and compression handling

### Processing and AI

- Custom content processor and semantic extractor
- Content-type classification
- Markdown/structured document representations
- Selective media analysis
- Ollama cloud vision/text models
- NVIDIA embedding model through an OpenAI-compatible API
- Heading-aware and content-type-aware chunking
- SHA-256 content hashes and duplicate protection
- Hybrid vector plus PostgreSQL full-text retrieval

## 12. Verified bugs, inconsistencies, and operational risks

The following issues are visible in the current implementation and should be treated as engineering follow-up items.

### High impact

1. **Keyword search index is not populated by the chunk insert path.** Retrieval requires `c.content_tsv IS NOT NULL` and searches it with `plainto_tsquery`, but `chunk_document()` does not insert or update `content_tsv`. Unless the live database has a trigger or generated column not shown in this repository, newly ingested chunks have no keyword-search representation. A manual SQL update has been used to backfill it, which indicates this is currently operationally significant. Add a generated column/trigger or update `content_tsv` during insertion and migration.

2. **Embedding configuration is inconsistent across code and schema history.** The embedding worker expects 2048 dimensions and retrieval casts to `halfvec`, while the base schema and historical migration comments contain `vector(1536)`, then `vector(2048)`, then `halfvec(2048)`. The live database must be treated as the authority and migrations should be consolidated. A mismatch can make inserts or vector queries fail.

3. **A processing failure can leave the chat permanently incomplete.** `process_document()` marks the document failed and the progress tracker failed, but it does not decrement `pending_documents`. The counter is decremented by the embedder, so a failed processing or chunking path can leave the shared counter above zero and prevent normal completion accounting.

4. **Missing embedding credentials also leave document accounting incomplete.** `embed_chunks()` marks progress failed and returns immediately when `EMBEDDING_MODEL_API_KEY` is absent. It does not decrement `pending_documents`, so multi-page jobs can remain in an inconsistent state.

### Medium impact

5. **The page ID is not passed into the Redis scraping job.** The API creates `page_id`, but the call to `add_scraping_job()` omits its `page_id` argument. The crawler later receives `page_id=None`. The crawler currently creates its own page rows, so this can produce duplicate page records and disconnect the initial API-created page from the crawled version.

6. **The queue is written twice through different mechanisms.** `add_scraping_job()` manually `LPUSH`es the JSON job onto `scraping_queue` and also calls `crawl_website.send()`. Dramatiq itself manages actor messages. If another consumer reads the raw list, the job may be duplicated; if no consumer reads it, the list is misleading dead data. One queue mechanism should be authoritative.

7. **The configured crawl depth is not enforced.** `MAX_CRAWL_DEPTH` exists but is unused. `_crawl_page()` sets `current_depth = 0` for every page and always adds links at depth 1. The actual behavior is a page-count-limited BFS, not a depth-limited crawl.

8. **Scheme-sensitive and subdomain-sensitive domain checks may omit expected pages.** `https://example.com` and `http://example.com` are different domains to the frontier, and `www.example.com` is not considered the same as `example.com`. This is stricter than many users expect.

9. **The crawler always creates a page rather than reusing one.** `CrawlerStorage.get_or_create_page()` explicitly always inserts a new row. The method name and the API's earlier page creation imply reuse, but duplicate ingestion can create multiple page records for the same chat and URL.

10. **The fetch strategy cache is per fetcher/crawl, not persistent.** Site strategy decisions are lost after the crawl. The repository contains Redis helpers for processed domains and boilerplate patterns, but the fetcher does not persist its HTTPX/Playwright/Bright Data decision there.

11. **Bright Data strategy helpers are only partially used.** `_should_use_brightdata()` and `_should_use_brightdata_for_url()` exist, but the main `fetch()` path primarily checks whether Bright Data has been tried and invokes it as a generic fallback. The intended cached Bright Data strategy is not consistently used as a first-choice strategy for later pages.

12. **No robots.txt policy is enforced.** The code has request delay but no visible robots.txt retrieval or allow/disallow evaluation. This can cause the crawler to request pages that the site has excluded for automated clients.

### Lower impact and maintainability risks

13. **Playwright treats only HTTP 200 as successful.** Pages returning other successful status codes, such as 204 or 206 where applicable, are marked unsuccessful even if they contain usable content.

14. **The HTTPX/Brotli handling is defensive but difficult to verify.** HTTPX commonly decompresses responses automatically, while the code conditionally attempts Brotli decompression based on a first-byte check. This can produce avoidable warnings or fail on unusual responses, although the fallback generally preserves the request path.

15. **The SQL entity filter is interpolated into query text.** Retrieval builds an `entity_clause` with an f-string containing `entity_filter`. If this parameter becomes user-controlled, it is a SQL-injection risk. It should be parameterized.

16. **Embedding vectors are silently padded or truncated.** The worker changes returned vectors to the configured 2048 dimensions instead of rejecting an unexpected model dimension. Padding or truncating a model vector can reduce retrieval quality and conceal a deployment/configuration error.

17. **The crawler persists raw HTML before processing, but the original API page row is not the same row used by crawler storage.** This split makes page/version relationships harder to reason about and complicates idempotent retries.

18. **The current automated fetcher tests cover strategy selection only.** `tests/test_fetcher_site_strategy.py` checks Playwright strategy reuse and SPA detection, but there are no equivalent end-to-end tests covering Redis enqueueing, page persistence, processing failure accounting, `content_tsv` population, chunking, or embedding completion.

## 13. Recommended correctness checks

Before relying on ingestion in production, verify:

1. A new chunk receives a non-null `content_tsv` automatically.
2. The live `chunks.embedding` type is exactly compatible with both the 2048-value writer and the retrieval cast.
3. A processing failure transitions the chat and all counters to a terminal state.
4. A missing embedding key produces an explicit failed job without leaving pending counters.
5. Replaying the same Dramatiq message does not create duplicate pages or decrement counters twice.
6. The initial page created by the API and the page/version created by the crawler have an intentional relationship.
7. The deployment has separate, clearly named Dramatiq queues for crawling, processing, chunking, and embedding.
8. Crawl scope, robots policy, redirects, subdomains, and URL query parameters match the product's intended policy.

## 14. End state

A successful ingestion produces:

- A completed Redis crawl job with crawl statistics.
- Page and page-version records.
- A processed document with cleaned searchable content and metadata.
- Optional media asset records and AI-generated descriptions.
- One or more chunk records with heading/type metadata.
- Completed embeddings stored in PostgreSQL.
- WebSocket progress and completion notifications.

At that point, questions in the same chat can use hybrid retrieval over the ingested website corpus.
