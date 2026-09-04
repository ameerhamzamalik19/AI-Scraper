# AI Scraper Backend: Agent Handoff

This document is the complete working brief for an AI coding agent. The agent may receive only this file and must assume it has no access to the original repository. Treat statements about current behavior as observations from the handoff snapshot, not guarantees: inspect the supplied code before changing it.

## Project Identity

This is the backend for **Universal Scraper**, a FastAPI service that accepts a website URL, crawls pages on the same domain, stores page versions and processed content, chunks content, generates embeddings, and answers questions using retrieval.

Repository root in the original workspace:

```text
backend/
```

Primary language: Python 3.12 target (the current local test run used Python 3.14).

## Repository Layout

```text
main.py                         FastAPI application and WebSocket endpoint
config.py                       Environment-backed application/crawler settings
models.py                       Pydantic request/response models
exceptions.py                   Application exceptions
database.py                     Async asyncpg pool
 database_sync.py               Synchronous PostgreSQL helpers
redis_config.py                 Redis connection and queue names
redis_client.py                 Redis job API
redis_pubsub.py                 Redis pub/sub support
websocket_manager.py            WebSocket connection manager

api/deps.py                     Request dependencies, including user header handling
api/routes/chats.py             Chat endpoints
api/routes/process.py           Main POST /api/process-link workflow
api/routes/retrieval_pipeline.py Retrieval/RAG answer path
api/routes/scraping.py          GET /api/scraping-jobs/{chat_id}
api/routes/users.py              User endpoints
api/routes/websocket.py         WebSocket-related routes

crawler/crawler.py              BFS crawl orchestrator
crawler/fetcher.py              HTTPX/Playwright fetching and render-mode detection
crawler/frontier.py             Crawl URL frontier and same-domain behavior
crawler/parser.py               HTML metadata/link extraction
crawler/storage.py               Persistence of pages and page versions
crawler/content_processor.py    Current BeautifulSoup content extraction

processors/chunker.py           Enhanced structure-aware chunking
processors/content_processor_old.py Legacy processor; do not assume it matches crawler/content_processor.py

services/                       Database-backed user/project/chat/message/page/scraping services
workers/crawler_worker.py       Dramatiq crawl actor
workers/processor_worker.py    HTML/content processing actor, optional Ollama vision analysis
workers/chunker_worker.py      Chunk persistence actor
workers/embedder_worker.py     NVIDIA embedding actor

db/schema.sql                   PostgreSQL + pgvector schema/reference SQL
tests/                          Pytest tests
docker-compose.yml              API, workers, Redis, and PostgreSQL services
Dockerfile                      Python image with Playwright Chromium
requirements.txt                Pinned Python dependencies
```

## Runtime Architecture

The normal flow is:

1. Client sends `POST /api/process-link` with a URL in `LinkRequest`.
2. `api/routes/process.py` obtains or creates the user, default project, and chat.
3. It creates a page record and puts a scraping job in Redis through `redis_client.add_scraping_job`.
4. `workers/crawler_worker.py` consumes the Dramatiq scraping queue.
5. `crawler/crawler.py` performs a same-domain BFS crawl, normally limited by `crawler_settings.MAX_PAGES_PER_CRAWL` (currently 5).
6. `crawler/fetcher.py` chooses HTTPX or Playwright. A JavaScript app-shell/site decision can be cached and reused per domain.
7. `crawler/storage.py` writes pages and page versions to PostgreSQL. Each fetched version references a document.
8. The processor actor cleans/processes HTML using `crawler/content_processor.py`, writes document content/metadata, and dispatches chunking.
9. `workers/chunker_worker.py` uses `processors/chunker.py` to create useful, unique, heading-aware chunks.
10. `workers/embedder_worker.py` generates vector embeddings through the NVIDIA OpenAI-compatible API and updates chunk embedding status.
11. `api/routes/retrieval_pipeline.py` retrieves relevant chunks and generates an answer for later chat questions.
12. Redis-backed chat status and WebSocket broadcasts report progress.

Important current behavior: `Crawler._crawl_page` directly sends `process_document` after creating a version, and `crawler_worker.py` also sends processing for the first crawled document after the crawl. Check idempotency before changing this fan-out because duplicate processing may be possible.

## Services and Queues

Docker Compose defines:

- `main`: FastAPI on container port 8000, published as `localhost:8000`.
- `crawler`: Dramatiq actor `workers.crawler_worker`.
- `processor`: Dramatiq actor `workers.processor_worker`.
- `chunker`: Dramatiq actor `workers.chunker_worker`.
- `embedder`: Dramatiq actor `workers.embedder_worker`.
- `redis`: Redis 7, published as `localhost:6379`.
- `postgres`: `pgvector/pgvector:pg18`, container port 5432, published as host `localhost:5433`.

Queue names from `redis_config.py`:

```text
scraping_queue
processing_queue
chunking_queue
embedding_queue
```

Inside Docker, the application connects to `redis:6379` and `postgres:5432`. From the host, PostgreSQL is `localhost:5433`; do not confuse the two ports.

## Local Setup

Prerequisites:

- Python 3.12 recommended.
- Docker Desktop and Docker Compose for Redis/PostgreSQL and the full worker topology.
- Playwright Chromium and its OS dependencies if running outside Docker.
- PostgreSQL with the `vector` extension if not using Compose.
- Ollama/NVIDIA credentials only for real embedding/vision processing.

Create an environment and install dependencies:

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

The PostgreSQL volume is named `postgres_data`; `docker compose down -v` removes persisted database data and should be treated as destructive.

Run the API directly for a local-only process:

```powershell
python main.py
```

Equivalent development server:

```powershell
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The Dockerfile installs all Python requirements and runs `playwright install --with-deps chromium`.

## Environment Variables

`config.py` calls `load_dotenv()` and supplies defaults:

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

For Docker Compose, the services set:

```text
DATABASE_URL=postgresql://postgres:postgres@postgres:5432/universal_scraper
REDIS_URL=redis://redis:6379/0
```

Additional model variables used in worker code include:

```text
OLLAMA_API_KEY=<needed for Ollama cloud vision calls>
OLLAMA_VISION_MODEL=gemma4:31b-cloud
EMBEDDING_MODEL_API_KEY=<needed for NVIDIA embeddings>
```

Without `EMBEDDING_MODEL_API_KEY`, the embedder actor marks the chat failed rather than producing real embeddings. Do not put secrets in source control or in this document.

## API Surface

Confirmed entry points:

- `GET /` returns an API-running message.
- `GET /health` returns `{"status": "healthy"}`.
- `POST /api/process-link` accepts a link or question. A new chat must begin with a valid URL; an existing chat cannot receive a second URL.
- `GET /api/scraping-jobs/{chat_id}` returns scraping jobs for a chat.
- `WS /ws/{chat_id}` joins a chat room and receives status/message broadcasts.
- Additional chat, user, and WebSocket routes are registered from `api/routes/` and should be read before changing contracts.

Use the OpenAPI page at `http://localhost:8000/docs` when the API is running. Request/response shape is defined in `models.py`, not in this document.

## Data Model

The intended PostgreSQL model in `db/schema.sql` includes users, projects, crawl jobs, pages, page versions, documents, chunks, crawl URLs, processing events, and worker failures. PostgreSQL uses UUIDs and pgvector. Important relationships:

```text
user -> projects -> pages -> page_versions -> documents -> chunks
project -> crawl_jobs -> crawl_urls
```

Before relying on `db/schema.sql`, verify the SQL is active and complete in the supplied snapshot. The visible schema is heavily commented reference SQL, so database initialization/migrations may be handled elsewhere or may be incomplete.

There is a known dimension risk: the schema reference declares `embedding vector(1536)`, while `workers/embedder_worker.py` currently uses dimension `2048` and model `nvidia/llama-nemotron-embed-vl-1b-v2`. Any embedding change must reconcile the database vector dimension, model output, SQL casts, and retrieval queries together.

## Content Processing Rules

`crawler/content_processor.py` is the current processor used by `workers/processor_worker.py`:

- Removes non-content tags such as script, style, iframe, SVG, metadata, head, and template.
- Learns navigation/header/footer-like text for the first page of a domain.
- Stores learned boilerplate patterns in Redis under `boilerplate_patterns:<domain>`.
- Attempts conservative similarity-based boilerplate removal on subsequent pages.
- Extracts title, headings, paragraphs, lists, tables, sections, metadata, all visible text, and a document structure.
- Deduplicates sections and heading paths.

Be conservative when modifying boilerplate removal: deleting valid content is worse than retaining a small amount of navigation. Domain cache behavior must be tested with Redis unavailable and available.

## Testing and Quality Checks

Run the full suite:

```powershell
python -m pytest -q
```

Run a focused test file:

```powershell
python -m pytest -q tests/test_fetcher_site_strategy.py
```

Useful checks before submitting a change:

```powershell
python -m compileall -q .
python -m pytest -q tests/test_fetcher_site_strategy.py
```

Do not claim the full suite is green without running it. The recorded baseline in this snapshot is currently **not collectible**: `tests/test_content_processor.py` imports `ContentProcessor` from `processors.content_processor_old`, but that module exposes `ContentProcessorOld`. This causes an ImportError during collection. The same test file also appears written for an older processor contract and expects cards behavior that may not match the current processor. Treat this as pre-existing until a change explicitly addresses it.

Tests may require external services if expanded to integration coverage. Prefer unit tests with mocked Redis/database/network clients for pure logic and focused regression tests for crawler, parser, processor, queue dispatch, and status transitions.

## Engineering Rules for the Agent

- Read the owning module and its nearest tests before editing.
- Preserve public APIs and existing data contracts unless the task requires a deliberate migration.
- Fix root causes and keep changes narrow; do not perform unrelated cleanup.
- Never hardcode credentials, API keys, or machine-specific absolute paths.
- Do not use destructive database commands or remove the Docker volume without explicit approval.
- Be careful with async code: database pool lifecycle, `asyncio.run` inside worker actors, and WebSocket cleanup are intentional boundaries that need targeted tests.
- Keep Redis and PostgreSQL access mockable in unit tests.
- When changing queue payloads or actor signatures, update every producer and consumer together.
- When changing schema fields, update storage, workers, retrieval SQL, and any migration/bootstrap path together.
- Preserve same-domain crawl restrictions, page limits, request delays, response-size limits, and retry behavior unless explicitly requested.
- Use logging consistently; avoid adding noisy `print` calls in new code unless matching an existing worker diagnostic is necessary.
- Avoid broad HTML extraction rewrites when the task concerns one field or one selector.
- After the first edit, run the narrowest relevant test or compile check immediately. Then run the broader available checks.
- Report pre-existing failures separately from failures introduced by the change.

## First Investigation Checklist

1. Confirm the working directory and inspect `git status`.
2. Identify the exact requested behavior and its route, worker, service, or processor owner.
3. Read the owning implementation plus one adjacent test/call site.
4. Run the narrowest available check before editing.
5. Make the smallest compatible change.
6. Run the focused check, then `python -m compileall -q .` and relevant pytest tests.
7. Review the diff for accidental API, schema, dependency, or configuration changes.

## Known Snapshot Risks

- Test collection fails because of the legacy processor import mismatch described above.
- The current and legacy content processors have different contracts; do not silently substitute one for the other.
- Processing may be enqueued from both crawler layers; verify duplicate dispatch/idempotency.
- Database schema embedding dimension and embedder dimension disagree.
- Docker PostgreSQL uses host port 5433 but container port 5432.
- Redis boilerplate cache is domain-scoped and can affect tests across runs; clear or isolate cache state when testing processor behavior.
- The default local database URL in `config.py` uses placeholder credentials and port 5432, while Compose uses different credentials and host/container addressing.
