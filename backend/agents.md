# AI Scraper Project Guide

## 1. Project overview

This project is a backend service for turning websites into searchable, AI-ready knowledge sources. The system accepts a URL from a user, crawls the target website, extracts content, processes the pages, breaks content into chunks, stores embeddings, and later answers questions based on the scraped content.

The product is designed around a "website as a knowledge base" concept:

- User sends a URL
- Backend validates and stores the session/project
- A crawler discovers pages within the same domain
- A processing pipeline cleans and structures page content
- Content is chunked and embedded
- Retrieval uses similarity search to find relevant chunks
- An LLM answers the user based on those chunks

This is not just a scraper. It is a retrieval-augmented generation (RAG) pipeline built around website content.

---

## 2. High-level architecture

The application is built as a modular backend with a central FastAPI API and several background workers managed through Redis + Dramatiq.

Core architectural pieces:

- FastAPI app: REST API + WebSocket server
- PostgreSQL: primary relational database and vector storage
- Redis: job broker, status tracking, and pub/sub messaging
- Dramatiq workers: async background job execution
- Playwright + HTTPX: page fetching and browser rendering
- BeautifulSoup + custom HTML parsing: link extraction and content cleanup
- Ollama / OpenAI-compatible models: AI processing and Q&A generation
- WebSockets: real-time status updates to clients

The app intentionally separates concerns:

- API layer handles user requests
- Worker layer performs computationally heavy jobs
- Database layer persists jobs, pages, documents, chunks, and embeddings
- Redis layer provides queue coordination and live messaging

---

## 3. Core technologies in use

### Python ecosystem
- Python 3.x
- FastAPI for the HTTP API and WebSocket endpoints
- Pydantic for request/response validation
- Asyncio for asynchronous database and network work

### Database and vector search
- PostgreSQL
- pgvector extension for embedding vectors
- asyncpg for async PostgreSQL access
- SQL schema supports users, projects, chat sessions, pages, page versions, documents, chunks, and crawl frontier data

### Queueing and messaging
- Redis as broker and pub/sub system
- Dramatiq for background tasks
- Redis job keys for storing scraping and processing metadata

### Scraping and browser automation
- httpx for fast HTTP fetching
- Playwright for JS-heavy pages and stealth-browser rendering
- playwright-stealth for anti-bot masking attempts
- BeautifulSoup for HTML parsing and link extraction
- readability-lxml / trafilatura / markdownify for cleaner extraction workflows

### AI / embeddings / generation
- Ollama Python client
- OpenAI Python SDK
- OpenAI-compatible model usage via NVIDIA NIM / custom endpoints
- LLMs are used in two key contexts:
  1. Document processing and content understanding
  2. Answering user questions over retrieved documents

### Real-time updates
- FastAPI WebSockets
- Redis Pub/Sub
- custom WebSocket manager for broadcasting status and progress

### Containerization
- Docker
- Docker Compose
- PostgreSQL and Redis are run as separate services

---

## 4. Runtime configuration and startup

The app starts from the main entrypoint in [main.py](main.py).

This file:

- creates the FastAPI app
- defines the startup/lifespan lifecycle
- captures the main event loop for thread-safe Redis/WebSocket publishing
- ensures Redis is initialized at startup
- registers the API routers
- exposes health endpoints on / and /health

Startup path:

1. Application boots with Uvicorn
2. FastAPI app is created with CORS enabled
3. Redis manager is initialized
4. Database pool is set up on demand
5. WebSocket and REST endpoints become available

The containerized setup in [docker-compose.yml](docker-compose.yml) runs separate services for:

- main API
- crawler worker
- processor worker
- chunker worker
- embedder worker
- Redis
- PostgreSQL with pgvector

This is a classic distributed worker model: the API responds to requests while the queue handles expensive work asynchronously.

---

## 5. Configuration model

The project configuration is in [config.py](config.py).

It defines:

- database connection string
- Redis URL and queue names
- default user/project defaults
- CORS origins
- crawler limits and timeouts

Important settings include:

- MAX_PAGES_PER_CRAWL
- MAX_CRAWL_DEPTH
- REQUEST_TIMEOUT
- BROWSER_TIMEOUT
- USER_AGENT
- MAX_RETRIES
- RETRY_DELAY

The Redis configuration in [redis_config.py](redis_config.py) sets queue names such as:

- scraping_queue
- processing_queue
- chunking_queue
- embedding_queue

Job TTL is configured there as well.

---

## 6. Database schema and domain model

The schema in [db/schema.sql](db/schema.sql) defines the core data model.

Major entities:

### Users
- id
- email
- created_at
- updated_at

### Projects
- project belongs to a user
- stores user-level workspaces or scraping scopes

### Chat sessions
- conversation record for a user/project
- likely used to tie questions, URLs, page context, and AI messages together

### Crawl jobs
- a job for crawling a website
- status can be DISCOVERED, FETCHING, FETCHED, PROCESSING, PROCESSED, CHUNKING, CHUNKED, EMBEDDING, INDEXED, or failure states
- tracks start URL, page limits, timeouts, and progress metadata

### Pages
- each discovered page has a unique record per project
- normalized URL ensures duplicate prevention

### Page versions
- stores the fetched version of a page
- tracks status_code, content_type, raw content hash, fetch method, processing status, and size

### Documents
- processed or cleaned page content
- stored in markdown format
- metadata is preserved as JSONB

### Chunks
- content split into smaller semantic sections
- includes chunk type, content, heading path, token count, embedding status, and embedding vector
- designed for retrieval over vector similarity

### Crawl URL frontier
- tracks discovered URLs in the crawl graph
- has status values like PENDING, QUEUED, FETCHING, FETCHED, FAILED

### Processing events / worker failures
- auditing and observability for pipeline stages
- useful for debugging failed jobs and retries

This schema is designed around a true pipeline: crawl -> page version -> document -> chunk -> embedding -> retrieval.

---

## 7. API layer and request flow

The project exposes routes under /api and related modules.

### API route modules
- [api/routes/process.py](api/routes/process.py): primary user input processing
- [api/routes/chats.py](api/routes/chats.py): chat management
- [api/routes/scraping.py](api/routes/scraping.py): scraping job queries
- [api/routes/users.py](api/routes/users.py): user info
- [api/routes/websocket.py](api/routes/websocket.py): WebSocket route

### Key request lifecycle

#### 1. User sends a URL
The endpoint /api/process-link accepts a LinkRequest.

It does the following:

- validates the input
- ensures the user exists
- ensures a project exists
- decides whether the input is a URL or a question
- checks if the first message in a chat is a valid URL
- creates or reuses a chat session
- stores the user message
- emits the message over WebSocket

#### 2. URL flow
If the input includes a valid URL:

- creates a page record
- updates chat progress status to crawling
- enqueues a scraping job in Redis
- returns a response indicating the job has been accepted

#### 3. Question flow
If the input is a text question:

- marks progress as processing
- runs a retrieval pipeline against the indexed content for the chat/project
- returns a natural-language answer from the LLM

This means the system is built around the idea of a conversation whose first message is a site URL and subsequent messages are questions about that site.

---

## 8. Scraping workflow

The scraping flow starts with the worker in [workers/crawler_worker.py](workers/crawler_worker.py).

### How it works

1. A job is enqueued in Redis.
2. Dramatiq picks up the job.
3. The crawler worker loads job metadata from Redis.
4. It creates a Crawler instance.
5. The crawler fetches the initial URL and discovers links.
6. It manages a breadth-first URL frontier.
7. Each page is fetched, parsed, stored, and sent to the processor worker.

### Main crawler orchestration
The class in [crawler/crawler.py](crawler/crawler.py) is the orchestration engine.

Responsibilities:

- maintain a URL frontier
- fetch pages via the fetcher
- parse HTML and extract internal links
- persist page and version records
- dispatch processing tasks
- track crawl progress and statuses
- broadcast crawl progress over WebSocket

### Fetcher behavior
The fetcher in [crawler/fetcher.py](crawler/fetcher.py) is robust and hybrid:

- first tries httpx for fast HTTP fetches
- falls back to Playwright when needed
- can detect Cloudflare challenge pages and browser-rendered content
- can apply stealth browser options
- can support browser-based rendering for hard-to-fetch sites

It normalizes content, decodes compressed responses, and extracts metadata.

### HTML parsing
The HTML parser in [crawler/parser.py](crawler/parser.py) does:

- domain extraction
- URL normalization
- internal-link discovery
- title / meta description / canonical URL extraction
- basic content extraction from HTML text blocks

The parser is intentionally lightweight; large-scale cleaning and semantic work happens later in the processing step.

---

## 9. Processing pipeline

Once a page is fetched and stored, the crawler triggers `process_document` through the Dramatiq processor worker.

The processor worker in [workers/processor_worker.py](workers/processor_worker.py) is responsible for turning raw HTML into structured, usable content.

### Processing tasks include
- metadata extraction
- cleaning HTML
- converting raw page content into markdown
- identifying headings, structure, paragraphs, lists, and media
- optional image analysis with Ollama vision models
- generating summaries or structured descriptions for media
- storing processed documents in the database

The processor uses:

- BeautifulSoup
- markdownify
- Pillow / image handling
- Ollama vision-based analysis for non-text media
- custom heuristics to avoid analyzing decorative or tiny images

This stage is where the raw website content becomes an AI-readable document.

---

## 10. Chunking and embeddings

The schema and workers indicate the project expects a standard RAG pipeline after processing.

The chunker worker is designed to:

- take processed document content
- split it into chunks by structure and token limits
- assign chunk type values such as text, table, list, code, quote, mixed
- store heading path and chunk metadata
- compute or schedule embeddings

The embedder worker then:

- generates embedding vectors for chunks
- stores them in pgvector columns
- marks embedding status as COMPLETED or FAILED

This allows the later retrieval stage to run similarity searches against the content.

The retrieval code in [api/routes/retrieval_pipeline.py](api/routes/retrieval_pipeline.py) demonstrates the retrieval logic:

- create a standalone question from chat history
- generate an embedding for the user question
- search for relevant chunks in the database
- apply scoring and category boosting
- retrieve the most relevant content
- provide the content to an LLM for final answer generation

This is the heart of the RAG system.

---

## 11. Retrieval and question answering workflow

The actual answer flow is built around the chat history and the scraped page corpus.

### Typical Q&A flow

1. The user asks a question in an existing chat.
2. The system checks whether the chat already has a URL context.
3. It retrieves message history.
4. It rewrites the follow-up question into a standalone question if needed.
5. It embeds the question.
6. It searches the database for relevant stored chunks.
7. It re-ranks and filters the chunks.
8. It passes the retrieved text to the LLM prompt.
9. The model generates a final answer using only that information.

The system uses a strict prompt policy:

- answer naturally
- do not mention “the context” or “chunks”
- do not cite sources
- answer only from the retrieved content
- if the information is missing, say so simply

This is a classic retrieval-augmented answer layer.

---

## 12. WebSocket and real-time progress model

The WebSocket infrastructure is important for UX.

### Files involved
- [websocket_manager.py](websocket_manager.py)
- [utils/websocket_manager.py](utils/websocket_manager.py)
- [redis_pubsub.py](redis_pubsub.py)
- [utils/chat_status_tracker.py](utils/chat_status_tracker.py)
- [utils/progress_tracker.py](utils/progress_tracker.py)

### How it works

- each chat has a Redis channel such as ws:chat:<chat_id>
- workers publish progress or summary messages to Redis
- WebSocket manager forwards those to connected clients
- the frontend can subscribe to per-chat progress updates

This allows the user interface to display:

- crawl progress
- page completion counts
- worker updates
- failure states
- final crawl summary

The project explicitly uses Redis Pub/Sub instead of only in-process messaging so that background workers can notify the API layer and connected clients even when they are in different processes.

---

## 13. Worker architecture

The project uses multiple Dramatiq workers, each with a distinct job responsibility.

### Crawler worker
- [workers/crawler_worker.py](workers/crawler_worker.py)
- handles URL fetching and discovery
- orchestrates the crawl job
- updates status and progress

### Processor worker
- [workers/processor_worker.py](workers/processor_worker.py)
- handles content processing
- transforms raw pages into documents
- uses AI vision when needed

### Chunker worker
- likely handles splitting processed documents into chunk records
- integrates with DATABASE and queue flow

### Embedder worker
- generates vector embeddings for chunks
- writes vectors into PostgreSQL

This is a pipeline architecture: each worker handles one stage and collectively they move content from raw website -> retrievable knowledge base.

---

## 14. Data flow end-to-end

Here is the end-to-end lifecycle of the application:

1. User sends a URL or a question to FastAPI
2. API validates and creates or reuses a chat record
3. If URL:
   - page record created
   - Redis job enqueued
4. Dramatiq crawler worker starts
5. Crawler fetches page HTML
6. HTML parser extracts links and metadata
7. Page/version/document records are written to PostgreSQL
8. A processing task is dispatched
9. Processor cleans and enriches document content
10. Chunks are created and embedded
11. Retrieval pipeline indexes the content
12. User conversation can trigger semantic Q&A against that corpus
13. Progress and notifications are broadcast through WebSocket

This is a full web-to-knowledge pipeline.

---

## 15. Key project conventions and patterns

### Redis-based job metadata
Jobs are stored in Redis as JSON-like metadata keyed by a job prefix. They may include:

- URL
- project_id
- user_id
- chat_id
- page_id
- timestamp
- status

### Thread-safe publishing
The application includes custom event loop bridging to allow publishing to Redis from non-async threads. This is explicitly important because background workers and threads need to send progress messages without breaking asynchronous event loop behavior.

### Defensive crawling
The fetcher includes:

- user-agent rotation
- Cloudflare challenge detection
- stealth browser injection
- browser fallback for difficult pages
- retry logic and request limits

### RAG-first design
Even though the app is named “AI Scraper,” its real value is in combining web crawling with semantic retrieval and question answering.

---

## 16. Project responsibilities by folder

### Root files
- [main.py](main.py): application entrypoint
- [config.py](config.py): app configuration
- [database.py](database.py): database pool management
- [database_sync.py](database_sync.py): synchronous DB utility layer
- [redis_config.py](redis_config.py): Redis queue names and connection config
- [redis_client.py](redis_client.py): Redis wrapper for jobs and status
- [websocket_manager.py](websocket_manager.py): central real-time messaging manager

### API
- [api/routes](api/routes): HTTP endpoints for process, chat, scraping, users, websocket
- [api/deps.py](api/deps.py): dependency helpers

### Crawler
- [crawler/fetcher.py](crawler/fetcher.py): fetch + render logic
- [crawler/parser.py](crawler/parser.py): HTML parsing and link extraction
- [crawler/frontier.py](crawler/frontier.py): crawl frontier and managing discovered URLs
- [crawler/storage.py](crawler/storage.py): persistence of crawl data
- [crawler/crawler.py](crawler/crawler.py): overall crawl orchestration

### Workers
- [workers/crawler_worker.py](workers/crawler_worker.py): scraping jobs
- [workers/processor_worker.py](workers/processor_worker.py): document processing
- [workers/chunker_worker.py](workers/chunker_worker.py): chunk creation
- [workers/embedder_worker.py](workers/embedder_worker.py): embedding generation

### Services
- [services/chat_service.py](services/chat_service.py): chat logic
- [services/message_service.py](services/message_service.py): message persistence
- [services/page_service.py](services/page_service.py): page operations
- [services/project_service.py](services/project_service.py): project management
- [services/scraping_service.py](services/scraping_service.py): scraping stats and job queries
- [services/user_service.py](services/user_service.py): user creation and lookup

### Utils
- [utils/chat_status_tracker.py](utils/chat_status_tracker.py): chat progress model
- [utils/progress_tracker.py](utils/progress_tracker.py): progress staging
- [utils/helpers.py](utils/helpers.py): utility helpers
- [utils/detectors.py](utils/detectors.py): URL and content detection
- [utils/validators.py](utils/validators.py): validation logic

---

## 17. Development and deployment notes

### Containerized local environment
The Docker Compose setup runs Redis and PostgreSQL and launches the app plus worker services. This is the intended local dev/test environment.

Important environment assumptions:

- API is exposed on port 8000
- PostgreSQL is exposed on host port 5433 and container port 5432
- Redis runs on port 6379
- PostgreSQL image uses pgvector

### Local database setup
The schema file includes PostgreSQL commands and pgvector extension setup. The database is designed to support vector search and RAG semantics.

### Dependencies
The project uses a fairly modern stack and includes libraries for:

- scraping
- browser automation
- AI models
- vector search
- async database access
- real-time messaging

The requirements file shows that the stack is deliberately chosen around reliability and compatibility rather than the newest breaking versions.

---

## 18. Practical summary

This project is best understood as a website-to-RAG platform.

The core idea is:

- ingest a site
- extract the useful knowledge from it
- store that knowledge structurally
- search it semantically
- answer questions in natural language

In practical terms, the backend is a queue-driven AI crawler with:

- FastAPI REST + WebSocket interface
- PostgreSQL for persistence and vector storage
- Redis + Dramatiq for asynchronous background jobs
- Playwright/HTTPX domain crawling
- BeautifulSoup-based extraction
- Ollama/OpenAI-compatible AI processing
- semantic retrieval for user Q&A

This is a real backend pipeline built for running website ingestion and retrieval at scale, even though it is still structured as a modular prototype / application service.

---

## 19. Recommended mental model for future contributors

When working on this project, think of it as a pipeline with five phases:

1. Request intake
2. Site crawling
3. Content processing
4. Chunking + embedding
5. Retrieval + question answering

If a bug appears, ask which stage failed:

- Request validation problem?
- Crawl/fetch issue?
- Page processing issue?
- Chunking/embedding issue?
- Retrieval or answer generation issue?

This mental model makes the system much easier to debug because each layer has a distinct job, data model, and worker.

---

## 20. Final note

The codebase is ambitious and does a lot of things at once: crawling, database persistence, WebSocket updates, queue orchestration, document cleaning, retrieval, and AI response generation. The architecture is intentionally modular so each of those concerns can evolve independently.

The core value proposition is not just “scrape a website,” but “turn a website into an AI-searchable knowledge base that can answer questions from that site’s content.”
