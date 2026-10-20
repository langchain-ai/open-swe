"""One task worker per async launch request"""

from alembic import op

revision = "4a2daa9d9f74"
down_revision = "8cb312f3b529"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE task_delegation ADD COLUMN launch_request_id text")
    op.execute(
        "CREATE UNIQUE INDEX task_delegation_launch_request "
        "ON task_delegation(coordinator_thread_id, launch_request_id) "
        "WHERE launch_request_id IS NOT NULL"
    )


def downgrade() -> None:
    raise NotImplementedError
