"""Add retry-safe identities for atomic tenant quota reservations.

Revision ID: 0005_atomic_tenant_quotas
Revises: 0004_embedding_schema_alignment
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_atomic_tenant_quotas"
down_revision: str | None = "0004_embedding_schema_alignment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE usage_events ADD COLUMN IF NOT EXISTS quota_identity VARCHAR(180)")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_usage_event_quota_identity'
                  AND conrelid = 'usage_events'::regclass
            ) THEN
                ALTER TABLE usage_events
                ADD CONSTRAINT uq_usage_event_quota_identity
                UNIQUE (tenant_id, quota_identity);
            END IF;
        END $$
        """
    )


def downgrade() -> None:
    # Data-preserving RAG rollback policy: old application versions ignore the
    # nullable column and the uniqueness constraint remains safe.
    pass
