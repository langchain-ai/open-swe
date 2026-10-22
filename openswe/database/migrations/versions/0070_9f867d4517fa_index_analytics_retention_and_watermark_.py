"""Index analytics retention and watermark scans.

The retention sweep and each summary recompute's data watermark read
``events``, ``outbox`` and ``ingestion_receipts`` whole: no index led with
the columns they filter on. Each table holds about a million rows, so every
replica's sweep held a pooled connection for seconds.
"""

from alembic import op

revision = "9f867d4517fa"
down_revision = "c8d8a5c65df3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS events_occurred_at_idx ON events (occurred_at)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS events_workspace_recorded_idx "
        "ON events (workspace_id, recorded_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS outbox_acknowledged_idx ON outbox (acknowledged_at) "
        "WHERE state = 'acknowledged'"
    )


def downgrade() -> None:
    raise NotImplementedError
