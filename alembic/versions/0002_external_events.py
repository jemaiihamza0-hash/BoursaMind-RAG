"""Apply the idempotent external-events extension."""

from pathlib import Path

from alembic import op

revision = "0002_external_events"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute((Path(__file__).parents[2] / "migrations" / "0002_external_events.sql").read_text())


def downgrade() -> None:
    # Data-preserving migration policy: no destructive automatic downgrade.
    pass
