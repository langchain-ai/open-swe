"""Event log"""

from alembic import op

revision = "ec8eaeed6158"
down_revision = "8114633625db"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE event_log (
            received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            source text NOT NULL,
            endpoint text NOT NULL,
            event_type text NOT NULL DEFAULT '',
            delivery_id text NOT NULL DEFAULT '',
            payload jsonb NOT NULL
        ) PARTITION BY RANGE (received_at)
        """
    )
    op.execute("CREATE INDEX event_log_delivery_idx ON event_log (source, delivery_id)")


def downgrade() -> None:
    raise NotImplementedError
