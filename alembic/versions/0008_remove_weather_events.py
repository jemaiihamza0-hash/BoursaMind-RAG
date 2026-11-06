"""Remove persisted Open-Meteo events.

Revision ID: 0008_remove_weather_events
Revises: 0007_constraint_names

The shared external_events table remains because INS, GDELT and Meta use it.
Deleted weather rows can only be recovered from a database backup.
"""

from alembic import op

revision = "0008_remove_weather_events"
down_revision = "0007_constraint_names"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM external_events WHERE source = 'open_meteo' OR event_type = 'weather'")


def downgrade() -> None:
    # Schema is unchanged and deleted provider records cannot be reconstructed.
    pass
