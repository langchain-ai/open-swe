"""Workspace breakout destination"""

from alembic import op

revision = "1a27b64154a3"
down_revision = ["c8e1ce5e9cbe", "61e8a2c88c54"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE workspace ADD COLUMN breakout_channel_id text")


def downgrade() -> None:
    op.execute("ALTER TABLE workspace DROP COLUMN breakout_channel_id")
