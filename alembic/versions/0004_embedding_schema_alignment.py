"""Align the migrated schema with RAG conversations and embedding spaces.

Revision ID: 0004_embedding_schema_alignment
Revises: 0003_ingestion_job_retries
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_embedding_schema_alignment"
down_revision: str | None = "0003_ingestion_job_retries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQL is deliberately idempotent because RAG downgrades preserve data and
    # only move the revision marker (see downgrade()).
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id SERIAL PRIMARY KEY,
            tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            workspace_id INTEGER REFERENCES workspaces(id),
            external_id VARCHAR(255) NOT NULL,
            turns JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_conversation_external_id UNIQUE (tenant_id, external_id)
        )
        """
    )
    op.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedding_dimension INTEGER")
    op.execute(
        """
        UPDATE document_chunks
        SET embedding_dimension = jsonb_array_length(embedding)
        WHERE embedding_dimension IS NULL AND jsonb_typeof(embedding) = 'array'
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM document_chunks
                WHERE embedding_dimension IS NULL OR embedding_dimension <= 0
            ) THEN
                RAISE EXCEPTION
                    'document_chunks contains an invalid embedding; repair it before migrating';
            END IF;
        END $$
        """
    )
    op.execute("ALTER TABLE document_chunks ALTER COLUMN embedding_dimension SET NOT NULL")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_document_chunks_embedding_dimension_positive'
                  AND conrelid = 'document_chunks'::regclass
            ) THEN
                ALTER TABLE document_chunks
                ADD CONSTRAINT ck_document_chunks_embedding_dimension_positive
                CHECK (embedding_dimension > 0);
            END IF;
        END $$
        """
    )

    indexes = {
        "ix_api_keys_prefix": "api_keys(prefix)",
        "ix_api_keys_key_hash": "api_keys(key_hash)",
        "ix_workspaces_slug": "workspaces(slug)",
        "ix_collections_workspace_id": "collections(workspace_id)",
        "ix_collections_slug": "collections(slug)",
        "ix_documents_collection_id": "documents(collection_id)",
        "ix_documents_external_id": "documents(external_id)",
        "ix_documents_content_hash": "documents(content_hash)",
        "ix_document_chunks_document_id": "document_chunks(document_id)",
        "ix_document_chunks_content_hash": "document_chunks(content_hash)",
        "ix_document_chunks_embedding_model": "document_chunks(embedding_model)",
        "ix_document_chunks_embedding_dimension": "document_chunks(embedding_dimension)",
        "ix_document_chunks_document_type": "document_chunks(document_type)",
        "ix_document_chunks_embedding_space": (
            "document_chunks(tenant_id, embedding_model, embedding_dimension)"
        ),
        "ix_ingestion_jobs_document_id": "ingestion_jobs(document_id)",
        "ix_usage_events_request_id": "usage_events(request_id)",
        "ix_usage_events_operation": "usage_events(operation)",
        "ix_usage_events_created_at": "usage_events(created_at)",
        "ix_conversations_external_id": "conversations(external_id)",
        "ix_investor_memories_user_id": "investor_memories(user_id)",
        "ix_investor_memories_memory_type": "investor_memories(memory_type)",
        "ix_investor_memories_ticker": "investor_memories(ticker)",
        "ix_investor_memories_source_event": "investor_memories(source_event)",
        "ix_external_events_tenant_id": "external_events(tenant_id)",
        "ix_external_events_source": "external_events(source)",
        "ix_external_events_event_type": "external_events(event_type)",
        "ix_external_events_location": "external_events(location)",
        "ix_external_events_company": "external_events(company)",
        "ix_external_events_sector": "external_events(sector)",
        "ix_external_events_indicator": "external_events(indicator)",
        "ix_external_events_metric": "external_events(metric)",
        "ix_external_events_published_at": "external_events(published_at)",
        "ix_external_events_observed_at": "external_events(observed_at)",
        "ix_external_events_collected_at": "external_events(collected_at)",
        "ix_external_events_external_id": "external_events(external_id)",
    }
    for name, expression in indexes.items():
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {expression}")


def downgrade() -> None:
    # RAG migration policy is data-preserving: conversations, embeddings, and
    # operational indexes remain in place. Production rollback uses a backup or
    # an application rollback compatible with this additive schema.
    pass
