"""Apply the existing idempotent baseline schema under Alembic control."""

from pathlib import Path

from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute((Path(__file__).parents[2] / "migrations" / "0001_initial.sql").read_text())


def downgrade() -> None:
    # Baseline tables may contain production data; rollback is intentionally not destructive.
    pass
