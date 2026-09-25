"""Inbound webhooks"""

from alembic import op

revision = "ec8eaeed6158"
down_revision = "8114633625db"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE inbound_webhooks (
            received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            source text NOT NULL CHECK (source IN ('github', 'slack', 'linear')),
            endpoint text NOT NULL,
            event_type text NOT NULL DEFAULT '',
            delivery_id text NOT NULL DEFAULT '',
            payload jsonb NOT NULL
        ) PARTITION BY RANGE (received_at)
        """
    )
    op.execute(
        "CREATE INDEX inbound_webhooks_delivery_idx ON inbound_webhooks (source, delivery_id)"
    )


def downgrade() -> None:
    raise NotImplementedError
