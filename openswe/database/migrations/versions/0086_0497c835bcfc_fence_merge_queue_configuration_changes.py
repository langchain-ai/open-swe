"""Fence merge queue configuration changes"""

from alembic import op

revision = "0497c835bcfc"
down_revision = "00ba3da33037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE merge_queue ADD COLUMN configuration_generation text")


def downgrade() -> None:
    raise NotImplementedError
