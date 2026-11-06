"""Name historical uniqueness constraints consistently.

Revision ID: 0007_constraint_names
Revises: 0006_shared_document_visibility

The SQL baseline predates Alembic and let PostgreSQL generate these names.
Only names change: columns, uniqueness semantics, indexes, and data are
preserved.  Explicit names make ORM metadata and the managed schema agree.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007_constraint_names"
down_revision: str | None = "0006_shared_document_visibility"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


RENAMES = (
    ("api_keys", "api_keys_tenant_id_prefix_key", "uq_api_key_prefix"),
    ("collections", "collections_workspace_id_slug_key", "uq_collection_workspace_slug"),
    (
        "document_chunks",
        "document_chunks_document_id_chunk_index_key",
        "uq_document_chunk_index",
    ),
    (
        "documents",
        "documents_tenant_id_collection_id_external_id_key",
        "uq_document_external_id",
    ),
    (
        "external_events",
        "external_events_tenant_id_source_external_id_key",
        "uq_external_event_identity",
    ),
    (
        "investor_memories",
        "investor_memories_tenant_id_user_id_memory_type_ticker_sour_key",
        "uq_investor_memory_source",
    ),
    ("workspaces", "workspaces_tenant_id_slug_key", "uq_workspace_tenant_slug"),
)


def _rename(table: str, old: str, new: str) -> None:
    op.execute(f'ALTER TABLE "{table}" RENAME CONSTRAINT "{old}" TO "{new}"')


def upgrade() -> None:
    for table, old, new in RENAMES:
        _rename(table, old, new)


def downgrade() -> None:
    for table, old, new in reversed(RENAMES):
        _rename(table, new, old)
