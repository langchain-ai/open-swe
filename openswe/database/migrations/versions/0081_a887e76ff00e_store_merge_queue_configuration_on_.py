"""Store merge queue configuration on workspace"""

from alembic import op

revision = "a887e76ff00e"
down_revision = "a7c12a7f1462"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE workspace ADD COLUMN merge_queue_config jsonb")


def downgrade() -> None:
    raise NotImplementedError
