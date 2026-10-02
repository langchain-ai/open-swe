"""Workspace default review channel"""

from alembic import op

revision = "a68e1a5430c2"
down_revision = ["1a27b64154a3", "52fab62a7608"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE workspace ADD COLUMN review_channel_id text")


def downgrade() -> None:
    op.execute("ALTER TABLE workspace DROP COLUMN review_channel_id")
