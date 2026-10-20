"""Track the run that launched an async task worker"""

from alembic import op

revision = "c5a6e6ef23fe"
down_revision = "4a2daa9d9f74"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE task_delegation ADD COLUMN IF NOT EXISTS launch_run_id text")
    op.execute("ALTER TABLE task_delegation ADD COLUMN IF NOT EXISTS launch_settled_at timestamptz")
    op.execute(
        "CREATE INDEX IF NOT EXISTS task_delegation_unsettled_launch ON task_delegation(launch_run_id) "
        "WHERE launch_run_id IS NOT NULL AND launch_settled_at IS NULL"
    )


def downgrade() -> None:
    raise NotImplementedError
