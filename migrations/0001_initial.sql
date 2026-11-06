CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS tenants (
    id SERIAL PRIMARY KEY,
    slug VARCHAR(80) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    contact_email VARCHAR(320),
    monthly_query_quota INTEGER NOT NULL DEFAULT 0,
    monthly_document_quota INTEGER NOT NULL DEFAULT 0,
    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS api_keys (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(120) NOT NULL,
    prefix VARCHAR(32) NOT NULL,
    key_hash VARCHAR(64) NOT NULL,
    scopes JSONB,
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, prefix)
);

CREATE TABLE IF NOT EXISTS workspaces (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    slug VARCHAR(80) NOT NULL,
    name VARCHAR(255) NOT NULL,
    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, slug)
);

CREATE TABLE IF NOT EXISTS collections (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    slug VARCHAR(80) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    metadata_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, slug)
);

CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    external_id VARCHAR(255),
    title VARCHAR(500) NOT NULL,
    source_url VARCHAR(1000),
    document_type VARCHAR(80) NOT NULL DEFAULT 'TEXT',
    content_hash VARCHAR(64) NOT NULL,
    text TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, collection_id, external_id)
);

CREATE TABLE IF NOT EXISTS document_chunks (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    token_count INTEGER NOT NULL DEFAULT 0,
    content_hash VARCHAR(64) NOT NULL,
    embedding JSONB NOT NULL,
    embedding_model VARCHAR(100) NOT NULL,
    page_number INTEGER,
    title VARCHAR(500),
    source_url VARCHAR(1000),
    document_type VARCHAR(80) NOT NULL DEFAULT 'TEXT',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_official BOOLEAN NOT NULL DEFAULT false,
    source_name VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id INTEGER REFERENCES workspaces(id) ON DELETE SET NULL,
    collection_id INTEGER REFERENCES collections(id) ON DELETE SET NULL,
    document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'QUEUED',
    chunks_created INTEGER NOT NULL DEFAULT 0,
    chunks_deleted INTEGER NOT NULL DEFAULT 0,
    embedding_tokens INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS usage_events (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    api_key_id INTEGER REFERENCES api_keys(id),
    request_id VARCHAR(36) NOT NULL,
    operation VARCHAR(40) NOT NULL,
    status_code INTEGER NOT NULL DEFAULT 200,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    embedding_tokens INTEGER NOT NULL DEFAULT 0,
    chunks_retrieved INTEGER NOT NULL DEFAULT 0,
    provider VARCHAR(60),
    error_code VARCHAR(80),
    flags JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_chunks_tenant_collection ON document_chunks(tenant_id, collection_id);
CREATE INDEX IF NOT EXISTS ix_chunks_metadata ON document_chunks USING GIN(metadata);
CREATE INDEX IF NOT EXISTS ix_usage_tenant_created ON usage_events(tenant_id, created_at);

CREATE TABLE IF NOT EXISTS investor_memories (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id VARCHAR(120) NOT NULL,
    memory_type VARCHAR(40) NOT NULL,
    ticker VARCHAR(40),
    company_name VARCHAR(255),
    source_event VARCHAR(120) NOT NULL,
    summary TEXT NOT NULL,
    evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence INTEGER NOT NULL DEFAULT 50,
    importance INTEGER NOT NULL DEFAULT 50,
    valid_from TIMESTAMPTZ,
    last_observed_at TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT true,
    is_user_edited BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, user_id, memory_type, ticker, source_event)
);

CREATE INDEX IF NOT EXISTS ix_investor_memories_scope
    ON investor_memories(tenant_id, user_id, ticker, memory_type);

CREATE TABLE IF NOT EXISTS external_events (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    source VARCHAR(40) NOT NULL,
    event_type VARCHAR(80) NOT NULL,
    title VARCHAR(500),
    description TEXT,
    location VARCHAR(255),
    company VARCHAR(255),
    sector VARCHAR(120),
    indicator VARCHAR(255),
    metric VARCHAR(120),
    value DOUBLE PRECISION,
    unit VARCHAR(40),
    period VARCHAR(120),
    published_at TIMESTAMPTZ,
    observed_at TIMESTAMPTZ,
    collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_url VARCHAR(1000),
    external_id VARCHAR(500),
    raw_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (tenant_id, source, external_id)
);

CREATE INDEX IF NOT EXISTS ix_external_events_tenant_source
    ON external_events(tenant_id, source, collected_at DESC);
