# Universal Scraper Backend: Agent Handoff

This file is the working guide for AI agents modifying this repository. It describes the current checkout, not the original design plan. Verify behavior in the owning module and nearby tests before making changes. Keep changes narrow, preserve existing contracts, and do not revert unrelated user work in a dirty worktree.

## Project Summary

Universal Scraper is a Python 3.12-targeted FastAPI backend for turning a website into searchable knowledge. A client submits a URL, the backend crawls same-site pages, stores raw and processed versions in PostgreSQL, chunks the content, generates vector embeddings, and answers follow-up questions with retrieval-augmented generation.

The repository is the `backend` folder. A sibling frontend exists at `../frontend/ai-scraper` and is a React/Vite client, but frontend changes are outside this backend handoff unless the task crosses the API contract.

## Current Repository Layout

```text
main.py                         FastAPI app, CORS, health routes, router registration
config.py                       Environment-backed settings and crawler limits
models.py                       Pydantic request and response models
exceptions.py                   Application exceptions
database.py                     Async asyncpg pool helpers
database_sync.py                Synchronous psycopg2 helpers
redis_config.py                 Redis URLs, queue names, job prefixes, TTLs
redis_client.py                 Redis job records and Dramatiq dispatch
redis_pubsub.py                 Redis pub/sub support
websocket_manager.py            WebSocket connection manager and broadcasts

api/deps.py                     Request dependencies and user-header parsing
api/routes/process.py           POST /api/process-link and URL/question routing
api/routes/chats.py             Chat, crawled-URL, and status endpoints
api/routes/scraping.py          Scraping-job endpoint
api/routes/users.py             Current-user endpoint
api/routes/websocket.py         WS /ws/{chat_id}
api/routes/retrieval_pipeline.py Retrieval and answer generation

crawler/crawler.py              BFS crawl orchestration and page persistence
crawler/fetcher.py              HTTPX/Playwright fetching and render detection
crawler/frontier.py             Same-site URL queue and URL filtering
crawler/parser.py               HTML metadata and link extraction
crawler/storage.py              Page, version, and document persistence
crawler/content_processor.py    Current BeautifulSoup content processor

processors/chunker.py           Enhanced structure-aware chunking
processors/content_processor_old.py Legacy processor used by old tests only
services/                       Database-backed user/project/chat/page/message services
utils/                          Detection, validation, progress, status, and helpers
workers/crawler_worker.py       Dramatiq crawl actor
workers/processor_worker.py    Dramatiq processing actor
workers/chunker_worker.py       Dramatiq chunking actor
workers/embedder_worker.py      Dramatiq embedding actor

universal_scraper_schema.sql    Current executable database dump/schema
db/schema.sql                   Obsolete/commented reference schema; not authoritative
tests/                          Unit and regression tests
docker-compose.yml              API, four workers, Redis, and pgvector PostgreSQL
Dockerfile                      Python image and Playwright Chromium setup
requirements.txt                Pinned runtime dependencies
ingestion_pipeline.md           Detailed ingestion walkthrough
universal_scraper_plan.md       Design/planning document; may describe future behavior
progress-bar.md                 Progress-stage behavior
websocket.md                    WebSocket and status behavior
commands.txt                    Manual local worker commands
```

The checkout may contain a local `venv/`, `.env`, `__pycache__/`, and `.pytest_cache/`. Do not commit generated files or secrets. Check `git status --short` before editing; preserve changes you did not make.

## Runtime Architecture

The active ingestion path is:

```text
POST /api/process-link
  -> create/retrieve user, project, and chat
  -> create a Redis job record
  -> enqueue Dramatiq crawl_website(job_id)
  -> Crawler.run() and same-site BFS crawl
  -> process_document(document_id) for each fetched page
  -> chunk_document(chat_id, document_id)
  -> embed_chunks(chat_id, document_id)
  -> retrieval_pipeline.answer_user_question() for questions
```

There are four independently launched Dramatiq workers:

| Actor | Module | Queue | Responsibility |
|---|---|---|---|
| `crawl_website` | `workers.crawler_worker` | `scraping_queue` | Run the crawl and update crawl/job state |
| `process_document` | `workers.processor_worker` | `processing_queue` | Clean HTML, extract structure/media, save document content |
| `chunk_document` | `workers.chunker_worker` | `chunking_queue` | Create and persist deduplicated heading-aware chunks |
| `embed_chunks` | `workers.embedder_worker` | `embedding_queue` | Call NVIDIA embeddings and save 2048-dimensional vectors |

`Crawler._crawl_page` dispatches `process_document` after a page version/document is created. The old handoff said `crawler_worker.py` also dispatches processing after the crawl; that code is currently commented out. Do not reintroduce or remove fan-out without checking idempotency and all producer/consumer paths.

`RedisClient.add_scraping_job()` writes a JSON job record to a Redis list and also calls `crawl_website.send(job_id)`. Dramatiq is the active worker transport; the Redis list is a redundant job/status record and must not be mistaken for a second worker system.

## Application Entry Points

`main.py` creates the FastAPI application, enables CORS from `CORS_ORIGINS`, and registers the routers. It runs on `0.0.0.0:8000` with reload when launched directly.

Active HTTP routes:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | API name, version, and healthy status |
| `GET` | `/health` | Returns `{"status": "healthy"}` |
| `POST` | `/api/process-link` | Submit a URL or ask a question |
| `GET` | `/api/chats` | List chats for the current configured user |
| `GET` | `/api/chats/{chat_id}` | Get one chat |
| `DELETE` | `/api/chats/{chat_id}` | Delete one chat |
| `GET` | `/api/chats/{chat_id}/crawled-urls` | Show per-URL crawl states |
| `GET` | `/api/chats/{chat_id}/status` | Show progress tracker state |
| `GET` | `/api/scraping-jobs/{chat_id}` | List scraping jobs |
| `GET` | `/api/users/current` | Get current user |

WebSocket:

```text
WS /ws/{chat_id}?user_id=<optional-user-id>
```

The server handles client message types `ping`, `get_status`, `get_users`, and `typing`. Unknown types are logged and ignored. The frontend may send `join`, which is currently ignored. WebSocket authentication/authorization is still a TODO.

The main request/response contracts live in `models.py`: `LinkRequest`, `ProcessLinkResponse`, `DetectionResult`, `MessageResponse`, `ChatResponse`, `ScrapingJobResponse`, and `UserResponse`. Preserve these public shapes unless the task explicitly changes the API.

`POST /api/process-link` rules:

- A new chat must begin with a valid URL.
- An existing chat cannot receive a second URL.
- URL submissions create a page and enqueue crawling asynchronously.
- Non-URL submissions call the retrieval pipeline synchronously in a worker thread.
- User and assistant messages are persisted and broadcast through the WebSocket manager.

## Crawler Behavior

`config.py` currently defines:

| Setting | Value | Notes |
|---|---:|---|
| `MAX_PAGES_PER_CRAWL` | `5` | Hard page limit |
| `MAX_CRAWL_DEPTH` | `3` | Declared but not enforced by the current BFS loop |
| `MAX_RESPONSE_SIZE` | `10 MB` | Response-size guard |
| `REQUEST_TIMEOUT` | `30 s` | HTTPX timeout |
| `BROWSER_TIMEOUT` | `60 s` | Playwright timeout |
| `REQUEST_DELAY` | `1 s` | Same-domain politeness delay |
| `MAX_RETRIES` | `3` | Fetch retries |
| `RETRY_DELAY` | `2 s` | Retry delay |

`crawler/frontier.py` keeps the exact scheme and host of the starting URL. It filters fragments, query URLs, common media/assets, archives, feeds, and document downloads. Confirm the frontier tests and implementation before changing same-domain behavior.

`crawler/fetcher.py` uses HTTPX first and can switch to Playwright for JavaScript app shells or blocked pages. The current implementation includes Playwright stealth support, Cloudflare detection, Brotli handling, and optional Bright Data integration. Render mode is cached per domain during a fetcher instance. The focused regression test is `tests/test_fetcher_site_strategy.py`.

`crawler/content_processor.py` is the active processor. It removes non-content tags, learns domain-level navigation/header/footer boilerplate, uses a Redis cache when available, and extracts titles, headings, paragraphs, lists, tables, metadata, visible text, and document structure. Boilerplate removal must remain conservative: retaining a little navigation is preferable to deleting valid page content.

## Database and Embeddings

`universal_scraper_schema.sql` is the current executable schema/dump for this checkout. It uses PostgreSQL, `uuid-ossp`, and pgvector. Relevant tables include `users`, `projects`, `chats`, `messages`, `pages`, `page_versions`, `documents`, `chunks`, `crawl_jobs`, `crawl_urls`, `crawled_urls`, `media_assets`, `processing_events`, and `worker_failures`.

Important current schema facts:

- `chunks.embedding` is `halfvec(2048)`.
- The embedding index uses `halfvec_cosine_ops`.
- `chats.pending_documents` is part of the active schema.
- `crawled_urls` has a unique `(chat_id, url)` constraint.
- The embedder uses `nvidia/llama-nemotron-embed-vl-1b-v2` and normalizes output to 2048 dimensions.

`db/schema.sql` is commented/reference material and declares a conflicting `vector(1536)` dimension. Do not use it as the migration or embedding source of truth. Any embedding change must update the provider model, dimension handling, database type/index, persistence SQL, and retrieval SQL together.

The database code has two access styles: async `asyncpg` in `database.py` and synchronous `psycopg2` helpers in `database_sync.py`. Preserve the existing async/sync boundary when changing services or workers.

## Docker and Local Setup

Recommended local setup on Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Start the complete stack:

```powershell
docker compose up --build
```

Stop it:

```powershell
docker compose down
```

`docker compose down -v` removes the `postgres_data` volume and is destructive; use it only with explicit approval.

Compose services and ports:

| Service | Command/role | Host mapping |
|---|---|---|
| `main` | FastAPI | `localhost:8000` -> `8000` |
| `crawler` | crawler Dramatiq worker | internal only |
| `processor` | processor Dramatiq worker | internal only |
| `chunker` | chunker Dramatiq worker | internal only |
| `embedder` | embedder Dramatiq worker | internal only |
| `redis` | Redis 7 | `localhost:6379` -> `6379` |
| `postgres` | `pgvector/pgvector:pg18` | `localhost:5433` -> `5432` |

Inside Compose, use `redis:6379` and `postgres:5432`. From the host, use Redis `localhost:6379` and PostgreSQL `localhost:5433`.

Run the API without Docker:

```powershell
python main.py
```

Or run workers manually in separate terminals:

```powershell
dramatiq workers.crawler_worker -p 1 -t 2 -v
dramatiq workers.processor_worker -p 1 -t 2 -v
dramatiq workers.chunker_worker -p 1 -t 2 -v
dramatiq workers.embedder_worker -p 1 -t 2 -v
```

The Dockerfile installs requirements and Playwright Chromium with OS dependencies. `requirements.txt` includes FastAPI, asyncpg, Redis, Dramatiq, HTTPX, Playwright, Playwright Stealth, BeautifulSoup, OpenAI-compatible clients, Ollama, extraction libraries, and PostgreSQL drivers.

## Environment Variables

`config.py` loads `.env` and provides these defaults:

```text
DATABASE_URL=postgresql://user:password@localhost:5432/universal_scraper
REDIS_URL=redis://localhost:6379/0
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
CORS_ORIGINS=http://localhost:5173
DEFAULT_USER_EMAIL=test@example.com
DEFAULT_PROJECT_NAME=Default Project
DEFAULT_PAGE_SIZE=50
SCRAPING_QUEUE_NAME=scraping_queue
```

Provider and crawler variables read elsewhere include:

```text
EMBEDDING_MODEL_API_KEY=<NVIDIA embedding API key>
OLLAMA_API_KEY=<Ollama vision API key>
OLLAMA_VISION_MODEL=<vision model override>
OPENROUTER_API_KEY=<optional provider key>
GROQ_API_KEY=<optional provider key>
BRIGHTDATA_API_KEY=<optional browser/proxy key>
BRIGHTDATA_ZONE_NAME=<optional Bright Data zone>
HARDCODED_USER_ID=<temporary test user override used by chat routes>
```

Never put real credentials in source, documentation, commits, or tool output. The local `.env` contains credential-looking values and should be treated as sensitive; rotate any key that has been exposed or committed. `HARDCODED_USER_ID` and the chat-route override are temporary testing behavior, not authentication. `api/deps.py` parses `X-User-ID`, but chat routes currently replace the dependency result with `HARDCODED_USER_ID`.

## Testing and Validation

Focused tests:

```powershell
python -m pytest -q tests/test_fetcher_site_strategy.py
python -m pytest -q tests/test_chunker.py
```

Full suite and syntax check:

```powershell
python -m pytest -q
python -m compileall -q .
```

The known baseline limitation is `tests/test_content_processor.py`: it imports `ContentProcessor` from `processors.content_processor_old`, while that module exposes `ContentProcessorOld`, and the test expects an older processor contract. Treat collection failures from that mismatch as pre-existing unless the task explicitly addresses the legacy test. Do not silently replace the active `crawler.content_processor.ContentProcessor` with the legacy processor.

The current `tests/test_chunker.py` baseline also fails because `EnhancedChunker().chunk_structure()` returns no chunks for its fixture. Investigate the chunker implementation and contract before attributing a future chunking failure to a new change.

`python -m compileall -q .` traverses the checked-in local `venv/` and currently reports a Python-2-style syntax error in `venv/Lib/site-packages/websocket/policyserver.py`. Prefer compiling application directories or use a clean environment when a syntax-only check is needed.

For changes to queues, run the relevant worker/unit tests and inspect every producer and consumer. For crawler changes, use mocked network/database clients where possible. For processor changes, test Redis available and unavailable paths and isolate the domain boilerplate cache. For schema or embedding changes, check schema, worker, storage, and retrieval SQL together.

## Known Risks and Open Work

- WebSocket authentication and authorization are not implemented.
- Chat routes currently use `HARDCODED_USER_ID` instead of the parsed user header.
- `MAX_CRAWL_DEPTH` is documented but not enforced by the BFS crawl.
- Redis job-list records and Dramatiq dispatch are both written; understand this redundancy before changing job status behavior.
- Provider calls require credentials for real embeddings and optional vision analysis. Missing embedding credentials result in zero/failure behavior in the embedder path; do not interpret that as a successful production ingestion.
- The active 2048-dimension `halfvec` schema conflicts with the obsolete 1536-dimension reference schema.
- Database bootstrap/migrations are not managed by a dedicated migration tool in this repository; verify which schema has actually been loaded before integration work.
- The sibling frontend currently targets the backend through its own Vite configuration and may have hardcoded development assumptions. Treat frontend and backend changes as one contract only when the task requires it.

## Agent Workflow

1. Run `git status --short` and identify user changes. Never reset or checkout unrelated files.
2. Find the owning route, worker, service, or processor and read its nearest test/call site.
3. State one local hypothesis about the behavior and run the cheapest check that could disprove it.
4. Make the smallest compatible edit. Preserve public APIs, queue payloads, and database contracts unless migration is intentional.
5. Immediately run the narrowest relevant test or compile check after the first edit.
6. Run broader relevant tests and `python -m compileall -q .` when practical.
7. Review the diff for accidental schema, dependency, configuration, generated-file, or secret changes.
8. Report pre-existing failures separately from failures introduced by the change.

Do not commit, create branches, delete database volumes, or rotate credentials unless the user explicitly asks for it.
