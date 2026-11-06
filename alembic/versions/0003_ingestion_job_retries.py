"""Add immutable retry linkage to RAG ingestion jobs.

Revision ID: 0003_ingestion_job_retries
Revises: 0002_external_events
"""

from pathlib import Path

from alembic import op

revision = "0003_ingestion_job_retries"
down_revision = "0002_external_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    migration = Path(__file__).parents[2] / "migrations" / "0003_ingestion_job_retries.sql"
    op.execute(migration.read_text())


def downgrade() -> None:
    # Job lineage is operational history; rollback intentionally keeps the data.
    pass
