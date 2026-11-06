"""Add durable owner grants and idempotent visibility operations.

Revision ID: 0006_shared_document_visibility
Revises: 0005_atomic_tenant_quotas
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_shared_document_visibility"
down_revision: str | None = "0005_atomic_tenant_quotas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS document_visibility_grants (
            id SERIAL PRIMARY KEY,
            tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            owner_key VARCHAR(180) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_document_visibility_owner
                UNIQUE (tenant_id, document_id, owner_key)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS visibility_operations (
            id SERIAL PRIMARY KEY,
            tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            operation_id VARCHAR(120) NOT NULL,
            external_id VARCHAR(255) NOT NULL,
            owner_key VARCHAR(180) NOT NULL,
            visible BOOLEAN NOT NULL,
            document_id INTEGER,
            document_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            chunks_deleted INTEGER NOT NULL DEFAULT 0,
            remaining_owners INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_visibility_operation UNIQUE (tenant_id, operation_id)
        )
        """
    )
    for name, expression in {
        "ix_document_visibility_grants_tenant_id": "document_visibility_grants(tenant_id)",
        "ix_document_visibility_grants_document_id": "document_visibility_grants(document_id)",
        "ix_document_visibility_grants_owner_key": "document_visibility_grants(owner_key)",
        "ix_visibility_operations_tenant_id": "visibility_operations(tenant_id)",
    }.items():
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {expression}")


def downgrade() -> None:
    # Additive, data-preserving rollback policy.
    pass
