"""Stop tracking async worker launch runs"""

from alembic import op

revision = "a7c12a7f1462"
down_revision = "fd87e6781048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Launched worker runs now report through the completion webhook.
    op.execute("DROP INDEX IF EXISTS task_delegation_unsettled_launch")
    op.execute("ALTER TABLE task_delegation DROP COLUMN IF EXISTS launch_settled_at")
    op.execute("ALTER TABLE task_delegation DROP COLUMN IF EXISTS launch_run_id")


def downgrade() -> None:
    raise NotImplementedError
