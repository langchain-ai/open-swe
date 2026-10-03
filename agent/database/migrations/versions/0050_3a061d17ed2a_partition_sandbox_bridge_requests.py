"""Partition sandbox bridge requests

Every command a bridged thread runs, and its output, is a row here, and a row
is dead once its waiter has read it. Daily partitions let the rotation drop a
whole day at a time instead of deleting rows one by one. Requests in flight
across the deploy are not worth carrying over: their waiters are on processes
this deploy replaces.

``client`` says which app serves a bridge, so the agent knows whether it owes
the CLI a printable result.
"""

from alembic import op

revision = "3a061d17ed2a"
down_revision = ["1a27b64154a3", "243390dbd39e", "f7b59c5091a8", "52fab62a7608"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE sandbox_bridge ADD COLUMN client text NOT NULL DEFAULT 'cli' "
        "CHECK (client IN ('cli', 'desktop'))"
    )
    op.execute("DROP TABLE sandbox_bridge_request")
    op.execute(
        """
        CREATE TABLE sandbox_bridge_request (
            request_id text NOT NULL,
            bridge_id text NOT NULL REFERENCES sandbox_bridge (bridge_id) ON DELETE CASCADE,
            method text NOT NULL,
            params jsonb NOT NULL,
            status text NOT NULL CHECK (status IN ('pending', 'claimed', 'done', 'failed')),
            result jsonb,
            error text,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            claimed_at timestamptz,
            finished_at timestamptz,
            PRIMARY KEY (request_id, created_at)
        ) PARTITION BY RANGE (created_at)
        """
    )
    op.execute(
        """
        CREATE INDEX sandbox_bridge_request_queue_idx
        ON sandbox_bridge_request (bridge_id, status, created_at)
        """
    )
    op.execute(
        "COMMENT ON TABLE sandbox_bridge_request IS 'One partition per UTC day "
        "(sandbox_bridge_request_YYYYMMDD); BridgeStore.ensure_partitions() creates today''s "
        "and tomorrow''s and drops everything older than yesterday.'"
    )


def downgrade() -> None:
    raise NotImplementedError
