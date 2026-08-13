# AI Scraper Backend - Agent Deep Dive Guide

## 1. Project Overview

**Universal Website Scraping + RAG (Retrieval-Augmented Generation) Platform**

A production-grade system that allows users to:
1. Submit public website URLs
2. Crawl and fetch website content
3. Parse and clean HTML into structured documents
4. Chunk content semantically
5. Embed chunks with vectors
6. Answer user questions using LLM with RAG grounded in website content

**Target Scale**: ~100 parallel users
**Status**: V1 Implementation (auth websites, CAPTCHA, multimodal excluded)

---

## 2. Technology Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| **Web Framework** | FastAPI | 0.141.1 |
| **Server** | Uvicorn | 0.52.1 |
| **Database** | PostgreSQL + pgvector | Latest |
| **Caching/Queue** | Redis | Latest |
| **DB Driver** | asyncpg | 0.31.0 |
| **Queue System** | Dramatiq + Redis | - |
| **HTTP Client** | HTTPX | - |
| **Browser Automation** | Playwright | - |
| **Config** | python-dotenv | 0.9.9 |

**Key Design Decision**: Dramatiq + Redis chosen over Celery for queue system.

---

## 3. Architecture Overview

### High-Level Pipeline

```
Client (Frontend)
    ↓
FastAPI (main.py)
    ↓
    ├─→ PostgreSQL (Source of Truth)
    ├─→ Redis (Dramatiq Broker)
    │   ├─→ Crawl Queue (HTTPX workers)
    │   ├─→ Browser Queue (Playwright workers)
    │   ├─→ Process Queue (HTML parsing)
    │   ├─→ Chunk Queue (Semantic chunking)
    │   └─→ Embed Queue (Vector embeddings)
    │   (All persist back to PostgreSQL)
    │
    └─→ Services Layer
        ├─→ UserService
        ├─→ ChatService
        ├─→ ProjectService
        ├─→ PageService
        ├─→ MessageService
        └─→ ScrapingService
```

### Queue Architecture

The pipeline is intentionally split into **independently executable queues**:

1. **Crawl Queue**
   - URL validation
   - HTTPX fetching
   - Response analysis
   - Decides if browser rendering needed
   - Page/version persistence
   - URL discovery
   - Enqueues browser or processing work
   - ❌ Does NOT chunk or embed

2. **Browser Queue**
   - Playwright rendering (resource-intensive)
   - Browser/page lifecycle management
   - Collects rendered HTML/DOM
   - Browser-specific retries & timeouts
   - Enqueues processing work
   - Isolated from other workers

3. **Process Queue**
   - HTML parsing & sanitization
   - Boilerplate detection
   - Main-content extraction
   - Document IR (Information Retrieval) creation
   - Canonical document generation
   - Extraction validation
   - Enqueues chunk work

4. **Chunk Queue**
   - Heading hierarchy analysis
   - Semantic block grouping
   - Token-aware chunking
   - Table/list/code handling
   - Chunk metadata assignment
   - Deterministic chunk hashes
   - Chunk persistence
   - Enqueues embedding work

5. **Embed Queue**
   - Selects pending chunks
   - Batching for efficiency
   - Embedding provider calls
   - Vector persistence (PostgreSQL + pgvector)
   - Model/dimension metadata tracking
   - Retries & rate limit handling

**Key Principle**: Each queue can be scaled independently based on workload.

---

## 4. Database Schema

### Core Tables

#### `users`
- **Purpose**: User authentication & identification
- **Columns**: `id` (UUID PK), `email` (unique), `created_at`, `updated_at`
- **Lifecycle**: Created on first API call or explicitly

#### `projects`
- **Purpose**: Logical grouping of scraping work per user
- **Columns**: `id` (UUID PK), `user_id` (FK), `name`, `created_at`, `updated_at`
- **Relationship**: 1 user → many projects

#### `pages` (Chat Sessions)
- **Purpose**: Represents a chat/conversation session
- **Columns**: `id` (UUID PK), `project_id` (FK), `url` (title), `normalized_url`, `created_at`, `updated_at`
- **Relationship**: 1 project → many pages/chats
- **Note**: Called "pages" but actually stores chat sessions

#### `page_versions`
- **Purpose**: Tracks different versions of fetched page content
- **Columns**:
  - `id` (UUID PK)
  - `page_id` (FK)
  - `status_code` (HTTP)
  - `content_type`
  - `raw_content_location` (object storage ref)
  - `content_hash` (SHA-256)
  - `fetch_method` (enum: 'httpx' or 'playwright')
  - `response_size`
  - `fetched_at`
  - `processing_status` (enum: PENDING, PROCESSING, COMPLETED, FAILED)
  - `created_at`, `updated_at`

#### `documents`
- **Purpose**: Clean/processed content derived from page versions
- **Columns**:
  - `id` (UUID PK)
  - `page_version_id` (FK, unique constraint)
  - `content` (Markdown format)
  - `content_format` (default 'markdown')
  - `metadata` (JSONB)
  - `processing_status` (PENDING, PROCESSING, COMPLETED, FAILED)
  - `created_at`, `updated_at`

#### `chunks`
- **Purpose**: Semantic chunks for RAG retrieval
- **Columns**:
  - `id` (UUID PK)
  - `page_version_id` (FK)
  - `document_id` (FK)
  - `chunk_index` (INT)
  - `chunk_type` (heading, paragraph, list, code, table, etc.)
  - `content` (TEXT)
  - `metadata` (JSONB: token_count, heading_hierarchy, etc.)
  - `chunk_hash` (deterministic)
  - `embedding_status` (PENDING, COMPLETED, FAILED)
  - `created_at`, `updated_at`

#### `crawl_jobs`
- **Purpose**: Top-level crawl job tracking
- **Columns**:
  - `id`, `project_id` (FK), `status` (enum: DISCOVERED, FETCHING, FETCHED, PROCESSING, etc.)
  - `start_url`, `max_pages`, `max_depth`, limits...
  - `started_at`, `completed_at`, `error_message`
  - `created_at`, `updated_at`

#### `crawl_urls`
- **Purpose**: URL frontier during crawl
- **Columns**: `id`, `crawl_job_id` (FK), `url`, `normalized_url`, `status`, `discovered_at`, timestamps

#### `messages`
- **Purpose**: Chat message history
- **Columns**: `id`, `chat_id` (FK to pages), `user_id` (FK), `role` ('user'/'assistant'), `content`, `is_url`, `url_processed`, timestamps

#### `message_embeddings`
- **Purpose**: Vector embeddings for messages
- **Columns**: `id`, `message_id` (FK), `embedding` (pgvector), `model`, `dimension`, timestamps

### Important Constraints & Patterns

- **UUIDs**: All IDs are UUID v4 (auto-generated by default)
- **Uniqueness**: `(project_id, normalized_url)` unique on pages; `page_version_id` unique on documents
- **Cascading Deletes**: Foreign keys use ON DELETE CASCADE
- **Processing Workflow**: Status enums flow through PENDING → PROCESSING → COMPLETED or FAILED
- **JSONB Metadata**: Used for flexible, unstructured data (chunk_type details, analysis results, etc.)

---

## 5. API Endpoints

### Core Routes

#### **Chats** (`/api/chats`)
- `POST /api/chats` - Create new chat/page
- `GET /api/chats` - List all chats for user
- `GET /api/chats/{chat_id}` - Get specific chat with messages

#### **Process** (`/api/process`)
- `POST /api/process-link` - Submit URL/content for processing
  - Input: `LinkRequest` (content, chat_id, user_id)
  - Output: `ProcessLinkResponse` (chat_id, user_id, project_id, message, detection, scraping_job_id)

#### **Scraping** (`/api/scraping-jobs`)
- `GET /api/scraping-jobs/{chat_id}` - Get scraping jobs for chat

#### **Users** (`/api/users`)
- `GET /api/users/me` - Get current user
- `GET /api/users/{user_id}` - Get specific user

#### **Health**
- `GET /` - Root endpoint (status check)
- `GET /health` - Health check

### Request/Response Models (from `models.py`)

**LinkRequest**
```python
{
    "content": str,           # URL or message (required, min_length=1)
    "chat_id": str,          # Optional conversation ID
    "user_id": str           # Optional user ID
}
```

**MessageResponse**
```python
{
    "id": str,
    "chat_id": str,
    "user_id": str,
    "role": str,            # 'user' or 'assistant'
    "content": str,
    "is_url": bool,
    "url_processed": bool,
    "created_at": str,
    "updated_at": str
}
```

**ChatResponse / ChatWithMessagesResponse**
```python
{
    "id": str,
    "title": str,
    "user_id": str,
    "project_id": str,
    "created_at": str,
    "updated_at": str,
    "messages": [MessageResponse]  # Optional
}
```

**ScrapingJobResponse**
```python
{
    "id": str,
    "url": str,
    "status": str,
    "error": str,
    "created_at": str,
    "updated_at": str
}
```

---

## 6. Services Architecture

### Service Classes & Responsibilities

#### **UserService** (`services/user_service.py`)
- `get_or_create_user(email=None) → str` - Get existing user or create default
- `get_user(user_id) → dict` - Fetch user details
- `get_current_user() → dict` - Get default/current user

**Key Pattern**: Uses `settings.DEFAULT_USER_EMAIL` for demo/development

#### **ProjectService** (`services/project_service.py`)
- `create_project(user_id, name) → dict` - Create user project
- `get_projects(user_id) → List[dict]` - List user's projects
- `get_or_create_default_project(user_id) → dict` - Get/create default project

#### **ChatService** (`services/chat_service.py`)
- `create_chat(user_id, project_id, title) → dict` - Create new chat
- `get_chats(user_id) → List[dict]` - Get all chats for user
- `get_chat_with_messages(chat_id) → dict` - Get chat with full message history
- `get_chat(chat_id) → dict` - Get single chat

#### **MessageService** (`services/message_service.py`)
- `create_message(chat_id, user_id, role, content, is_url=False) → dict` - Create message
- `get_messages(chat_id, limit=50) → List[dict]` - Get chat messages
- `mark_url_processed(message_id) → bool` - Mark URL as processed

#### **PageService** (`services/page_service.py`)
- `get_or_create_page(project_id, url) → dict` - Get/create page entry
- `create_page_version(page_id, status_code, content_type, ...) → dict` - Create page version
- `get_page_versions(page_id) → List[dict]` - Get all versions

#### **ScrapingService** (`services/scraping_service.py`)
- `create_scraping_jobs(urls, project_id, user_id) → str` - Enqueue scraping
- `get_scraping_jobs(chat_id, user_id) → List[dict]` - Get job status

### Data Flow Through Services

```
User Input (API Route)
    ↓
UserService (ensure user exists)
    ↓
ProjectService (get/create project)
    ↓
ChatService (get/create chat)
    ↓
MessageService (create message)
    ↓
ScrapingService (enqueue work → Redis)
    ↓
Database (persist state)
    ↓
Queue Workers (process async)
```

---

## 7. Key Components & Files

### Root Level Files

| File | Purpose |
|------|---------|
| `main.py` | FastAPI app initialization, router registration, CORS setup, health checks |
| `config.py` | Settings management (DATABASE_URL, REDIS_URL, CORS_ORIGINS, defaults) |
| `database.py` | PostgreSQL connection pool (asyncpg), lifecycle management |
| `models.py` | Pydantic models for all requests/responses |
| `exceptions.py` | Custom exception classes (DatabaseError, NotFoundError, etc.) |
| `redis_client.py` | Redis connection singleton |
| `redis_config.py` | Redis configuration (job prefix, TTL settings) |
| `requirements.txt` | Python dependencies |

### API Layer (`api/`)

| File | Purpose |
|------|---------|
| `routes/chats.py` | Chat CRUD endpoints |
| `routes/process.py` | URL/content processing endpoint |
| `routes/scraping.py` | Scraping job status endpoints |
| `routes/users.py` | User management endpoints |
| `deps.py` | Dependency injection (extracting headers, auth, etc.) |

### Services Layer (`services/`)

| File | Purpose |
|------|---------|
| `user_service.py` | User business logic |
| `project_service.py` | Project business logic |
| `chat_service.py` | Chat/conversation logic |
| `message_service.py` | Message creation/retrieval |
| `page_service.py` | Page & version management |
| `scraping_service.py` | Scraping queue integration |

### Utilities (`utils/`)

| File | Purpose |
|------|---------|
| `helpers.py` | `generate_uuid()`, `get_current_datetime()`, `safe_datetime_for_db()` |
| `validators.py` | URL validation, content validation |
| `detectors.py` | Content type detection (URL vs text, etc.) |

### Database (`db/`)

| File | Purpose |
|------|---------|
| `schema.sql` | PostgreSQL schema definition (commented out, for reference) |

---

## 8. Development Setup

### Prerequisites
- Python 3.10+
- PostgreSQL 14+
- Redis 6+
- Playwright (for browser automation)

### Environment Variables (`.env`)

```bash
# Database
DATABASE_URL=postgresql://user:password@localhost:5432/universal_scraper

# Redis
REDIS_URL=redis://localhost:6379/0

# API Config
CORS_ORIGINS=http://localhost:5173,http://localhost:3000
DEFAULT_USER_EMAIL=test@example.com
DEFAULT_PROJECT_NAME=Default Project
DEFAULT_PAGE_SIZE=50

# Scraping Config
SCRAPING_QUEUE_NAME=scraping_queue
```

### Installation & Running

```bash
# Install dependencies
pip install -r requirements.txt

# Initialize database (run schema.sql)
psql -U user -d universal_scraper -f db/schema.sql

# Run development server
python main.py

# Run with uvicorn directly
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Docker-Compose (Recommended)

For PostgreSQL and Redis:
```bash
docker-compose up -d postgres redis
```

---

## 9. Common Development Workflows

### Adding a New Endpoint

1. **Define Request/Response Models** in `models.py`
   ```python
   class MyRequest(BaseModel):
       field: str = Field(..., description="...")
   
   class MyResponse(BaseModel):
       id: str
       result: str
   ```

2. **Create/Update Service** in `services/my_service.py`
   ```python
   class MyService:
       @staticmethod
       async def do_work(param: str) -> dict:
           async with get_db_connection() as conn:
               # Query/insert logic
               pass
   ```

3. **Add Route** in `api/routes/my_route.py`
   ```python
   @router.post("/my-endpoint", response_model=MyResponse)
   async def my_endpoint(req: MyRequest):
       return await MyService.do_work(req.field)
   ```

4. **Register in `main.py`**
   ```python
   from api.routes import my_route
   app.include_router(my_route.router)
   ```

### Creating a Queue Task

1. **Enqueue from Service**
   ```python
   redis_client.add_scraping_job(url, project_id, user_id)
   ```

2. **Create Worker** (separate executable, not yet in repo)
   ```python
   from dramatiq import actor
   
   @actor
   def process_url(url, project_id):
       # Do work
       pass
   ```

3. **Run Worker**
   ```bash
   dramatiq my_worker_module
   ```

### Database Queries with asyncpg

```python
async with get_db_connection() as conn:
    # Single row
    row = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
    
    # Multiple rows
    rows = await conn.fetch("SELECT * FROM pages WHERE project_id = $1", project_id)
    
    # Scalar value
    count = await conn.fetchval("SELECT COUNT(*) FROM messages WHERE chat_id = $1", chat_id)
    
    # Insert/Update
    await conn.execute(
        "INSERT INTO users (id, email, created_at, updated_at) VALUES ($1, $2, $3, $4)",
        user_id, email, now, now
    )
```

---

## 10. Code Patterns & Conventions

### Datetime Handling

**Pattern Used**:
```python
from utils.helpers import get_current_datetime, safe_datetime_for_db

now = get_current_datetime()              # Returns datetime object
safe_now = safe_datetime_for_db(now)     # Ensures proper DB serialization
```

**Why**: PostgreSQL datetime handling requires careful type management with asyncpg

### UUID Generation

```python
from utils.helpers import generate_uuid

new_id = generate_uuid()  # Returns str UUID
```

### Database Connection Management

```python
from database import get_db_connection

async with get_db_connection() as conn:
    # Connection auto-closes on exit (context manager)
    result = await conn.fetchrow(...)
```

### Error Handling

```python
from exceptions import DatabaseError, NotFoundError

try:
    # Database operation
except Exception as e:
    raise DatabaseError(f"Failed to create user: {str(e)}")
```

### Service Layer Pattern

- Services are **static classes** with `@staticmethod` methods
- All database work happens in services, **never** in routes
- Services handle all business logic validation
- Routes are thin wrappers around services

### Response Models

- All responses use Pydantic `BaseModel`
- ISO format for dates: `created_at.isoformat()`
- UUID as strings (converted via `str(uuid)`)
- Optional fields marked as `Optional[Type] = None`

---

## 11. Important Configuration Values

| Setting | Default | Purpose |
|---------|---------|---------|
| `DATABASE_URL` | `postgresql://user:password@localhost:5432/universal_scraper` | PostgreSQL connection string |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string |
| `CORS_ORIGINS` | `http://localhost:5173` | Allowed frontend origins |
| `DEFAULT_USER_EMAIL` | `test@example.com` | Demo user email |
| `DEFAULT_PROJECT_NAME` | `Default Project` | Auto-created project name |
| `DEFAULT_PAGE_SIZE` | `50` | Pagination default |
| `REDIS_JOB_PREFIX` | `extraction_job:` | Redis key prefix for jobs |
| `JOB_TTL_SECONDS` | `86000` | Job expiration (~24 hours) |

### Limits (from schema)

| Setting | Default | Purpose |
|---------|---------|---------|
| `max_pages` | `100` | Max pages per crawl |
| `max_depth` | `3` | Max crawl depth |
| `max_response_size` | `10MB` | Single page size limit |
| `max_total_bytes` | `1GB` | Total crawl size limit |
| `max_concurrent_requests` | `5` | Concurrent HTTP requests |
| `max_browser_pages` | `1` | Concurrent browser instances |
| `request_timeout` | `30s` | HTTP timeout |
| `browser_timeout` | `60s` | Browser timeout |
| `per_domain_concurrency` | `2` | Requests per domain |

---

## 12. Development Practices & Guidelines

### Before Making Changes

1. **Understand the Data Model**
   - What tables are involved?
   - What are the relationships?
   - What status enums apply?

2. **Check Service Pattern**
   - Is there a service for this domain?
   - Add to existing or create new?

3. **Identify Queue Stages**
   - Does this involve async work?
   - Which queue stage should handle it?

### While Developing

1. **Follow the Service Layer Pattern**
   - API routes must be thin
   - All logic in services
   - Services work with database connections passed in or created internally

2. **Use Type Hints**
   - All function signatures must be typed
   - Use `Optional[]` for nullable values
   - Import types from `typing`

3. **Use Pydantic Models**
   - All inputs validated via BaseModel
   - All outputs use response models
   - No raw dicts in API responses

4. **Handle Errors Properly**
   ```python
   try:
       # Work
   except Exception as e:
       raise DatabaseError(f"Meaningful error: {str(e)}")
   ```

5. **Test Database Queries**
   - Use asyncpg parameter binding: `$1, $2, etc.`
   - Never string-format SQL
   - Parameterized queries prevent SQL injection

### Testing Locally

```bash
# Start services
docker-compose up -d

# Run server
python main.py

# Test endpoint
curl -X GET http://localhost:8000/api/chats

# View logs
docker-compose logs postgres
docker-compose logs redis
```

### Adding Tests (Future)

Create `tests/` directory with:
```
tests/
├── test_user_service.py
├── test_chat_service.py
├── test_api_routes.py
└── conftest.py  (fixtures, setup)
```

---

## 13. Next Steps for Development

### Immediate (Queue Workers)

The V1 plan specifies 5 separate queue workers that are NOT yet implemented:

1. **Crawl Worker** - HTTPX-based URL fetching
2. **Browser Worker** - Playwright rendering
3. **Process Worker** - HTML parsing & cleaning
4. **Chunk Worker** - Semantic chunking
5. **Embed Worker** - Vector embeddings

These should be separate executables in their own modules/packages.

### Short Term

- [ ] Implement queue workers (Dramatiq tasks)
- [ ] Add URL validation & sanitization
- [ ] Implement content detection (URL vs text)
- [ ] Add message embedding support
- [ ] Vector search integration

### Medium Term

- [ ] Add authentication (JWT or similar)
- [ ] Implement rate limiting
- [ ] Add request/response logging
- [ ] Set up monitoring & alerting
- [ ] Database indexing optimization

### Long Term

- [ ] CAPTCHA handling
- [ ] JavaScript-heavy site support
- [ ] Multimodal content (images, PDFs)
- [ ] Multi-user permission system
- [ ] Project templates/presets

---

## 14. Quick Reference: Key Function Signatures

```python
# User Operations
await UserService.get_or_create_user(email="user@example.com") → str

# Project Operations  
await ProjectService.create_project(user_id, name) → dict
await ProjectService.get_or_create_default_project(user_id) → dict

# Chat Operations
await ChatService.create_chat(user_id, project_id, title) → dict
await ChatService.get_chats(user_id) → List[dict]
await ChatService.get_chat_with_messages(chat_id) → dict

# Message Operations
await MessageService.create_message(chat_id, user_id, role, content, is_url=False) → dict
await MessageService.get_messages(chat_id, limit=50) → List[dict]

# Scraping Operations
await ScrapingService.create_scraping_jobs(urls, project_id, user_id, conn) → str
await ScrapingService.get_scraping_jobs(chat_id, user_id) → List[dict]

# Database
await get_db_connection() → AsyncContextManager[asyncpg.Connection]
await get_db_pool() → asyncpg.pool.Pool

# Utilities
generate_uuid() → str
get_current_datetime() → datetime
safe_datetime_for_db(dt: datetime) → datetime
```

---

## 15. Debugging Tips

### Check Database Connection

```python
# In service method
try:
    async with get_db_connection() as conn:
        test = await conn.fetchval("SELECT 1")
        print(f"DB Connected: {test}")
except Exception as e:
    print(f"DB Error: {e}")
```

### Check Redis Connection

```python
from redis_client import redis_client

try:
    redis_client.ping()
    print("Redis Connected")
except Exception as e:
    print(f"Redis Error: {e}")
```

### View Server Logs

```bash
# Development with reload
python main.py

# Production with uvicorn
uvicorn main:app --log-level debug
```

### Database Query Debugging

Add logging to services:
```python
import logging
logger = logging.getLogger(__name__)
logger.info(f"Query result: {row}")
```

---

## 16. Architecture Decisions & Rationale

### Why Separate Queue Workers?

**Decision**: Each stage (crawl, browser, process, chunk, embed) is independent
**Rationale**:
- Resource isolation (browsers use lots of memory)
- Independent scaling (can run 10 crawl workers, 1 browser worker)
- Failure isolation (failure in browser doesn't block chunking queue)
- Language flexibility (future: Python for text processing, Node.js for JS rendering)

### Why PostgreSQL for Vectors?

**Decision**: pgvector extension instead of Pinecone/Weaviate
**Rationale**:
- Single source of truth (no sync issues)
- Simpler deployment & ops
- Sufficient for ~100 users
- Full-text search + vector search together
- Cost-effective for V1

### Why Dramatiq over Celery?

**Decision**: Dramatiq with Redis
**Rationale**:
- Simpler than Celery
- Less configuration overhead
- Redis-based (already needed for caching)
- Good for V1 scale
- Migration path to RabbitMQ exists

### Why UUID Over Auto-Increment?

**Decision**: UUID v4 for all primary keys
**Rationale**:
- Globally unique (no ID collision risk)
- Security (IDs not enumerable)
- Distributed-friendly (no central sequence needed)
- Matches modern API patterns

---

## 17. File Locations Quick Reference

```
c:\Users\magnus mage\Documents\GitHub\AI Scraper\backend\
├── main.py                    # App entry point
├── config.py                  # Settings
├── database.py                # DB pool
├── models.py                  # Pydantic schemas
├── exceptions.py              # Custom errors
├── redis_client.py            # Redis singleton
├── redis_config.py            # Redis settings
├── requirements.txt           # Dependencies
├── universal_scraper_plan.md  # V1 spec
│
├── api/
│   ├── __init__.py
│   ├── deps.py                # Dependency injection
│   └── routes/
│       ├── chats.py           # Chat endpoints
│       ├── process.py         # Processing endpoint
│       ├── scraping.py        # Job status endpoints
│       └── users.py           # User endpoints
│
├── services/                  # Business logic
│   ├── user_service.py
│   ├── project_service.py
│   ├── chat_service.py
│   ├── message_service.py
│   ├── page_service.py
│   └── scraping_service.py
│
├── utils/                     # Helpers
│   ├── helpers.py
│   ├── validators.py
│   └── detectors.py
│
└── db/
    └── schema.sql             # PostgreSQL schema
```

---

## 18. Common Errors & Solutions

| Error | Cause | Solution |
|-------|-------|----------|
| `asyncpg.exceptions.ServerError: current transaction is aborted` | Transaction error | Check SQL syntax, ensure proper parameterization |
| `redis.exceptions.ConnectionError` | Redis unavailable | Start Redis: `docker-compose up redis` |
| `psycopg2.OperationalError: could not translate host name` | DB unavailable | Start PostgreSQL: `docker-compose up postgres` |
| `ValueError: time data does not match format` | Datetime mismatch | Use `safe_datetime_for_db()` before DB insert |
| `KeyError: 'id'` | Missing field in response | Check if data exists in DB before accessing |
| `CORS error` | Frontend origin not allowed | Add to `CORS_ORIGINS` in `.env` |

---

This document provides agents with comprehensive knowledge to:
✅ Understand the complete architecture
✅ Locate any file or function
✅ Add new features following patterns
✅ Debug issues systematically
✅ Extend the queue system
✅ Make database changes safely
✅ Work with services properly

**Last Updated**: 2026-08-13
**Version**: 1.0
