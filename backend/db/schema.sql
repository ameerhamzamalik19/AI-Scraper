-- START COMMAND:
    --  psql -U postgres -p 5433

    -- \i 'C:/Users/magnus mage/Documents/GitHub/AI Scraper/backend/db/schema.sql'

-- -- ============================================================================
-- -- Universal Website Scraping + RAG Platform - V1 Database Schema
-- -- PostgreSQL + pgvector
-- -- ============================================================================

-- CREATE DATABASE ai_scraper_db;

-- -- Enable required extensions
-- CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
-- CREATE EXTENSION IF NOT EXISTS vector;

-- -- ============================================================================
-- -- 1. Users
-- -- ============================================================================
-- CREATE TABLE users (
--     id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
--     email VARCHAR(255) UNIQUE NOT NULL,
--     created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
--     updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
-- );

-- -- ============================================================================
-- -- 2. Projects
-- -- ============================================================================
-- CREATE TABLE projects (
--     id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
--     user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
--     name VARCHAR(255) NOT NULL,
--     created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
--     updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
--     CONSTRAINT fk_project_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
-- );

-- -- ============================================================================
-- -- 3. Crawl Jobs
-- -- ============================================================================
-- CREATE TABLE crawl_jobs (
--     id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
--     project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
--     status VARCHAR(50) NOT NULL DEFAULT 'DISCOVERED',
--     start_url TEXT NOT NULL,
--     max_pages INTEGER DEFAULT 100,
--     max_depth INTEGER DEFAULT 3,
--     max_response_size INTEGER DEFAULT 10485760, -- 10MB
--     max_total_bytes INTEGER DEFAULT 1073741824, -- 1GB
--     max_concurrent_requests INTEGER DEFAULT 5,
--     max_browser_pages INTEGER DEFAULT 1,
--     request_timeout INTEGER DEFAULT 30,
--     browser_timeout INTEGER DEFAULT 60,
--     per_domain_concurrency INTEGER DEFAULT 2,
--     started_at TIMESTAMP WITH TIME ZONE,
--     completed_at TIMESTAMP WITH TIME ZONE,
--     error_message TEXT,
--     created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
--     updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
--     CONSTRAINT fk_crawl_job_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
--     CONSTRAINT chk_crawl_job_status CHECK (
--         status IN (
--             'DISCOVERED',
--             'FETCHING',
--             'FETCHED',
--             'PROCESSING',
--             'PROCESSED',
--             'CHUNKING',
--             'CHUNKED',
--             'EMBEDDING',
--             'INDEXED',
--             'FETCH_FAILED',
--             'PROCESSING_FAILED',
--             'CHUNKING_FAILED',
--             'EMBEDDING_FAILED'
--         )
--     )
-- );

-- -- ============================================================================
-- -- 4. Pages
-- -- ============================================================================
-- CREATE TABLE pages (
--     id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
--     project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
--     url TEXT NOT NULL,
--     normalized_url TEXT NOT NULL,
--     created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
--     updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
--     CONSTRAINT fk_page_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
--     CONSTRAINT uq_page_project_normalized_url UNIQUE (project_id, normalized_url)
-- );

-- -- ============================================================================
-- -- 5. Page Versions
-- -- ============================================================================
-- CREATE TABLE page_versions (
--     id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
--     page_id UUID NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
--     status_code INTEGER,
--     content_type VARCHAR(255),
--     raw_content_location TEXT, -- For future object storage reference
--     content_hash VARCHAR(64), -- SHA-256 hash of raw content
--     fetch_method VARCHAR(20) NOT NULL, -- 'httpx' or 'playwright'
--     response_size INTEGER DEFAULT 0,
--     fetched_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
--     processing_status VARCHAR(50) DEFAULT 'PENDING',
--     created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
--     updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
--     CONSTRAINT fk_page_version_page FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE,
--     CONSTRAINT chk_fetch_method CHECK (fetch_method IN ('httpx', 'playwright')),
--     CONSTRAINT chk_processing_status CHECK (
--         processing_status IN (
--             'PENDING',
--             'PROCESSING',
--             'COMPLETED',
--             'FAILED'
--         )
--     )
-- );

-- -- ============================================================================
-- -- 6. Documents (Clean/Processed Content)
-- -- ============================================================================
-- CREATE TABLE documents (
--     id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
--     page_version_id UUID NOT NULL REFERENCES page_versions(id) ON DELETE CASCADE,
--     content TEXT NOT NULL, -- Cleaned content in Markdown format
--     content_format VARCHAR(20) DEFAULT 'markdown',
--     metadata JSONB DEFAULT '{}'::jsonb,
--     processing_status VARCHAR(50) DEFAULT 'PENDING',
--     created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
--     updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
--     CONSTRAINT fk_document_page_version FOREIGN KEY (page_version_id) REFERENCES page_versions(id) ON DELETE CASCADE,
--     CONSTRAINT uq_document_page_version UNIQUE (page_version_id),
--     CONSTRAINT chk_doc_processing_status CHECK (
--         processing_status IN (
--             'PENDING',
--             'PROCESSING',
--             'COMPLETED',
--             'FAILED'
--         )
--     )
-- );

-- -- ============================================================================
-- -- 7. Chunks
-- -- ============================================================================
-- CREATE TABLE chunks (
--     id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
--     page_version_id UUID NOT NULL REFERENCES page_versions(id) ON DELETE CASCADE,
--     document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
--     chunk_index INTEGER NOT NULL,
--     chunk_type VARCHAR(20) NOT NULL,
--     content TEXT NOT NULL,
--     context_prefix TEXT,
--     heading_path JSONB DEFAULT '[]'::jsonb,
--     token_count INTEGER,
--     chunk_hash VARCHAR(64) NOT NULL, -- SHA-256 hash for deduplication
--     embedding_status VARCHAR(50) DEFAULT 'PENDING',
--     embedding_model VARCHAR(100),
--     embedding_dimension INTEGER,
--     embedding vector(1536), -- Default dimension, will be configurable
--     created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
--     updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
--     CONSTRAINT fk_chunk_page_version FOREIGN KEY (page_version_id) REFERENCES page_versions(id) ON DELETE CASCADE,
--     CONSTRAINT fk_chunk_document FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
--     CONSTRAINT chk_chunk_type CHECK (
--         chunk_type IN (
--             'text',
--             'table',
--             'list',
--             'code',
--             'quote',
--             'mixed'
--         )
--     ),
--     CONSTRAINT chk_embedding_status CHECK (
--         embedding_status IN (
--             'PENDING',
--             'PROCESSING',
--             'COMPLETED',
--             'FAILED'
--         )
--     ),
--     -- Prevent duplicate chunks within the same document
--     CONSTRAINT uq_chunk_document_index UNIQUE (document_id, chunk_index),
--     -- Allow same chunk hash to appear in different documents
--     -- but prevent duplicates within the same page_version
--     CONSTRAINT uq_chunk_page_hash UNIQUE (page_version_id, chunk_hash)
-- );

-- -- ============================================================================
-- -- 8. Crawl URL Frontier
-- -- ============================================================================
-- CREATE TABLE crawl_urls (
--     id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
--     crawl_job_id UUID NOT NULL REFERENCES crawl_jobs(id) ON DELETE CASCADE,
--     url TEXT NOT NULL,
--     normalized_url TEXT NOT NULL,
--     depth INTEGER DEFAULT 0,
--     status VARCHAR(20) DEFAULT 'PENDING',
--     discovered_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
--     fetched_at TIMESTAMP WITH TIME ZONE,
--     created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
--     updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
--     CONSTRAINT fk_crawl_url_job FOREIGN KEY (crawl_job_id) REFERENCES crawl_jobs(id) ON DELETE CASCADE,
--     CONSTRAINT uq_crawl_job_url UNIQUE (crawl_job_id, normalized_url),
--     CONSTRAINT chk_crawl_url_status CHECK (
--         status IN (
--             'PENDING',
--             'QUEUED',
--             'FETCHING',
--             'FETCHED',
--             'FAILED'
--         )
--     )
-- );

-- -- ============================================================================
-- -- 9. Processing Events
-- -- ============================================================================
-- CREATE TABLE processing_events (
--     id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
--     entity_type VARCHAR(50) NOT NULL, -- 'page_version', 'document', 'chunk'
--     entity_id UUID NOT NULL,
--     event_type VARCHAR(50) NOT NULL, -- 'started', 'completed', 'failed', 'retry'
--     status VARCHAR(20) NOT NULL, -- 'success', 'failure', 'pending'
--     worker_id VARCHAR(255),
--     task_id VARCHAR(255),
--     message TEXT,
--     metadata JSONB DEFAULT '{}'::jsonb,
--     created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
--     CONSTRAINT chk_entity_type CHECK (entity_type IN ('page_version', 'document', 'chunk'))
-- );

-- -- ============================================================================
-- -- 10. Worker Failures
-- -- ============================================================================
-- CREATE TABLE worker_failures (
--     id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
--     entity_type VARCHAR(50) NOT NULL,
--     entity_id UUID NOT NULL,
--     worker_id VARCHAR(255),
--     task_id VARCHAR(255),
--     error_type VARCHAR(255),
--     error_message TEXT,
--     retry_count INTEGER DEFAULT 0,
--     max_retries INTEGER DEFAULT 3,
--     is_resolved BOOLEAN DEFAULT FALSE,
--     created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
--     updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
--     resolved_at TIMESTAMP WITH TIME ZONE,
--     CONSTRAINT chk_failure_entity_type CHECK (entity_type IN ('page_version', 'document', 'chunk'))
-- );

-- -- ============================================================================
-- -- 11. Indexes for Performance
-- -- ============================================================================

-- -- Users
-- CREATE INDEX idx_users_email ON users(email);

-- -- Projects
-- CREATE INDEX idx_projects_user_id ON projects(user_id);

-- -- Crawl Jobs
-- CREATE INDEX idx_crawl_jobs_project_id ON crawl_jobs(project_id);
-- CREATE INDEX idx_crawl_jobs_status ON crawl_jobs(status);

-- -- Pages
-- CREATE INDEX idx_pages_project_id ON pages(project_id);
-- CREATE INDEX idx_pages_normalized_url ON pages(normalized_url);

-- -- Page Versions
-- CREATE INDEX idx_page_versions_page_id ON page_versions(page_id);
-- CREATE INDEX idx_page_versions_content_hash ON page_versions(content_hash);
-- CREATE INDEX idx_page_versions_processing_status ON page_versions(processing_status);

-- -- Documents
-- CREATE INDEX idx_documents_page_version_id ON documents(page_version_id);

-- -- Chunks
-- CREATE INDEX idx_chunks_document_id ON chunks(document_id);
-- CREATE INDEX idx_chunks_page_version_id ON chunks(page_version_id);
-- CREATE INDEX idx_chunks_chunk_hash ON chunks(chunk_hash);
-- CREATE INDEX idx_chunks_embedding_status ON chunks(embedding_status);
-- CREATE INDEX idx_chunks_embedding ON chunks USING ivfflat (embedding vector_cosine_ops);

-- -- Crawl URLs
-- CREATE INDEX idx_crawl_urls_crawl_job_id ON crawl_urls(crawl_job_id);
-- CREATE INDEX idx_crawl_urls_status ON crawl_urls(status);

-- -- Processing Events
-- CREATE INDEX idx_processing_events_entity ON processing_events(entity_type, entity_id);
-- CREATE INDEX idx_processing_events_created_at ON processing_events(created_at);

-- -- Worker Failures
-- CREATE INDEX idx_worker_failures_entity ON worker_failures(entity_type, entity_id);
-- CREATE INDEX idx_worker_failures_is_resolved ON worker_failures(is_resolved);

-- -- ============================================================================
-- -- 12. Initial Data for Testing
-- -- ============================================================================

-- -- Insert a default test user (ID will be auto-generated)
-- INSERT INTO users (email) 
-- VALUES ('test@example.com')
-- ON CONFLICT (email) DO NOTHING;

-- -- ============================================================================
-- -- 13. Comments for Documentation
-- -- ============================================================================

-- COMMENT ON TABLE users IS 'Application users who own projects and data';
-- COMMENT ON TABLE projects IS 'User projects representing knowledge bases/crawl scopes';
-- COMMENT ON TABLE crawl_jobs IS 'Crawl jobs with configurable limits and lifecycle tracking';
-- COMMENT ON TABLE pages IS 'Logical pages (URLs) within a project scope';
-- COMMENT ON TABLE page_versions IS 'Individual fetches/versions of a page with raw content metadata';
-- COMMENT ON TABLE documents IS 'Cleaned/processed document content from a page version';
-- COMMENT ON TABLE chunks IS 'Document chunks with embeddings for retrieval';
-- COMMENT ON TABLE crawl_urls IS 'URL frontier for crawl job processing';
-- COMMENT ON TABLE processing_events IS 'Audit trail of processing events for debugging';
-- COMMENT ON TABLE worker_failures IS 'Track worker failures for retry and recovery';

-- COMMENT ON COLUMN chunks.embedding IS 'Vector embedding for the chunk content using pgvector';

-- COMMENT ON CONSTRAINT uq_chunk_page_hash ON chunks IS 'Prevents duplicate chunks within same page version for idempotent processing';

-- SELECT * FROM messages;

-- CREATE TABLE messages (
--     id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
--     chat_id UUID NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
--     user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
--     role VARCHAR(20) NOT NULL, -- 'user' or 'assistant'
--     content TEXT NOT NULL,
--     is_url BOOLEAN DEFAULT FALSE,
--     url_processed BOOLEAN DEFAULT FALSE,
--     created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
--     updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
--     CONSTRAINT chk_role CHECK (role IN ('user', 'assistant'))
-- );


-- -- Indexes
-- CREATE INDEX idx_messages_chat_id ON messages(chat_id);
-- CREATE INDEX idx_messages_user_id ON messages(user_id);
-- CREATE INDEX idx_messages_created_at ON messages(created_at);

-- CREATE TABLE chats (
--     id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
--     user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
--     page_id UUID REFERENCES pages(id) ON DELETE SET NULL,
--     title VARCHAR(255),
--     created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
--     updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
--     CONSTRAINT fk_chat_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
--     CONSTRAINT fk_chat_page FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE SET NULL
-- );

-- -- Indexes for performance
-- CREATE INDEX idx_chats_user_id ON chats(user_id);
-- CREATE INDEX idx_chats_page_id ON chats(page_id);
-- CREATE INDEX idx_chats_created_at ON chats(created_at);

-- COMMENT ON TABLE chats IS NULL;
-- COMMENT ON COLUMN chats.page_id IS NULL;

-- ALTER TABLE chats DROP COLUMN page_id;

-- ALTER TABLE chats ADD COLUMN project_id UUID REFERENCES projects(id) ON DELETE CASCADE;
-- CREATE INDEX idx_chats_project_id ON chats(project_id);


-- ALTER TABLE pages ADD COLUMN chat_id UUID REFERENCES chats(id) ON DELETE CASCADE;

-- -- Create index for performance
-- CREATE INDEX idx_pages_chat_id ON pages(chat_id);

-- -- Comment on column
-- COMMENT ON COLUMN pages.chat_id IS 'Chat that this page belongs to';

-- Add processing status to documents (if not exists)
-- ALTER TABLE documents ADD COLUMN IF NOT EXISTS processing_status VARCHAR(50) DEFAULT 'PENDING';
-- ALTER TABLE documents ADD COLUMN IF NOT EXISTS cleaned_content TEXT;
-- ALTER TABLE documents ADD COLUMN IF NOT EXISTS processed_at TIMESTAMP WITH TIME ZONE;

-- -- Add chunk metadata columns
-- ALTER TABLE chunks ADD COLUMN IF NOT EXISTS heading_path JSONB DEFAULT '[]';
-- ALTER TABLE chunks ADD COLUMN IF NOT EXISTS token_count INTEGER;
-- ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding_status VARCHAR(50) DEFAULT 'PENDING';

-- -- Add embedding vector column (if not exists)
-- ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding vector(1536);

-- -- Add embedding metadata
-- ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(100);
-- ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding_dimension INTEGER;

-- -- Index for vector similarity search
-- CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON chunks USING ivfflat (embedding vector_cosine_ops);

-- ALTER TABLE chunks DROP COLUMN embedding;

-- -- Add new vector column with 2048 dimensions
-- ALTER TABLE chunks ADD COLUMN embedding vector(2048);

-- -- Recreate the index
-- CREATE INDEX idx_chunks_embedding ON chunks USING ivfflat (embedding vector_cosine_ops);

-- ALTER TABLE chunks DROP COLUMN IF EXISTS embedding;

-- -- Add halfvec column with 2048 dimensions
-- ALTER TABLE chunks ADD COLUMN embedding halfvec(2048);

-- -- Create index with halfvec_cosine_ops
-- CREATE INDEX idx_chunks_embedding ON chunks USING ivfflat (embedding halfvec_cosine_ops);

-- CREATE TABLE IF NOT EXISTS media_assets (
--     id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
--     page_version_id UUID NOT NULL REFERENCES page_versions(id) ON DELETE CASCADE,
--     document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
--     media_type VARCHAR(20) NOT NULL,
--     source_url TEXT,
--     mime_type VARCHAR(100),
--     data_base64 TEXT,
--     description TEXT NOT NULL,
--     created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
--     CONSTRAINT chk_media_type CHECK (media_type IN ('image', 'table'))
-- );

-- CREATE INDEX IF NOT EXISTS idx_media_assets_document_id ON media_assets(document_id);
-- CREATE INDEX IF NOT EXISTS idx_media_assets_page_version_id ON media_assets(page_version_id);

-- SELECT * FROM chunks WHERE content LIKE '%backend%' ORDER BY created_at DESC LIMIT 5;
docker exec -it backend-postgres-1 pg_dump -U postgres -d universal_scraper --schema-only > universal_scraper_schema.sql

-- docker exec -it backend-postgres-1 psql -U postgres -d universal_scraper --schema-only > universal_scraper_schema.sql