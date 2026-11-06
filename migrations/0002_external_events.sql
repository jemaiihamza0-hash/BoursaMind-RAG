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
