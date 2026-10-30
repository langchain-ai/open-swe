"""Thread index: every thread's metadata, runtime and last known run status.

The source of truth for listing, reading and updating threads when
``THREAD_STORE=postgres``; the runtime a thread runs on (``local`` Agent Server
or ``mda``) keeps its runs, checkpoints and a copy of its metadata.
"""

from alembic import op

revision = "913a5deda4f3"
down_revision = "5b2d2caf7c5a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE thread_index (
            thread_id text PRIMARY KEY,
            runtime text NOT NULL CHECK (runtime IN ('local', 'mda')),
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            status text NOT NULL DEFAULT 'idle'
                CHECK (status IN ('idle', 'busy', 'interrupted', 'error')),
            status_since timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    op.execute(
        "CREATE INDEX thread_index_metadata ON thread_index USING gin (metadata jsonb_path_ops)"
    )
    op.execute("CREATE INDEX thread_index_updated_at ON thread_index (updated_at)")
    op.execute("CREATE INDEX thread_index_created_at ON thread_index (created_at)")
    op.execute(
        "CREATE INDEX thread_index_busy ON thread_index (status_since) WHERE status = 'busy'"
    )


def downgrade() -> None:
    raise NotImplementedError
