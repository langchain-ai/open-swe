"""The relay a laptop's CLI answers sandbox requests through.

A graph worker and the HTTP handler holding the CLI's long poll run on
different replicas, so the queue between them is these two tables. A request is
claimed with ``FOR UPDATE SKIP LOCKED`` and answered at most once, and the
bridge's heartbeat is what tells a waiter the CLI is still there rather than
gone for good.
"""

from alembic import op

revision = "e0af88280850"
down_revision = "1e04f9eabcb0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE sandbox_bridge (
            bridge_id text PRIMARY KEY,
            owner_login text NOT NULL,
            hostname text NOT NULL,
            root_path text NOT NULL,
            label text,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            last_heartbeat_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            closed_at timestamptz
        )
        """
    )

    op.execute("CREATE INDEX sandbox_bridge_owner_idx ON sandbox_bridge (owner_login)")

    # The prune sweep scans open bridges by how long they have been quiet.
    op.execute(
        """
        CREATE INDEX sandbox_bridge_heartbeat_idx
        ON sandbox_bridge (last_heartbeat_at)
        WHERE closed_at IS NULL
        """
    )

    op.execute(
        """
        CREATE TABLE sandbox_bridge_request (
            request_id text PRIMARY KEY,
            bridge_id text NOT NULL REFERENCES sandbox_bridge (bridge_id) ON DELETE CASCADE,
            method text NOT NULL,
            params jsonb NOT NULL,
            status text NOT NULL CHECK (status IN ('pending', 'claimed', 'done', 'failed')),
            result jsonb,
            error text,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            claimed_at timestamptz,
            finished_at timestamptz
        )
        """
    )

    # Serves the claim: the oldest unfinished requests of one bridge, in order.
    op.execute(
        """
        CREATE INDEX sandbox_bridge_request_queue_idx
        ON sandbox_bridge_request (bridge_id, status, created_at)
        """
    )


def downgrade() -> None:
    raise NotImplementedError
