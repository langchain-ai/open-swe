"""Catch the preview up on async launch columns.

The preview applied fd87e6781048 before a rebase chained 4a2daa9d9f74 and
c5a6e6ef23fe beneath it, so it never ran those two. Elsewhere they did, and this
does nothing. Preview only: drop it before this branch merges.
"""

from alembic import op

revision = "be497de2554d"
down_revision = "fd87e6781048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE task_delegation ADD COLUMN IF NOT EXISTS launch_request_id text")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS task_delegation_launch_request "
        "ON task_delegation(coordinator_thread_id, launch_request_id) "
        "WHERE launch_request_id IS NOT NULL"
    )
    op.execute("ALTER TABLE task_delegation ADD COLUMN IF NOT EXISTS launch_run_id text")
    op.execute("ALTER TABLE task_delegation ADD COLUMN IF NOT EXISTS launch_settled_at timestamptz")
    op.execute(
        "CREATE INDEX IF NOT EXISTS task_delegation_unsettled_launch "
        "ON task_delegation(launch_run_id) "
        "WHERE launch_run_id IS NOT NULL AND launch_settled_at IS NULL"
    )


def downgrade() -> None:
    raise NotImplementedError
