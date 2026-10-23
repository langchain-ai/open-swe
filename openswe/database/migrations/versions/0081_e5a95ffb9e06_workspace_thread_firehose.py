"""Workspace thread firehose"""

from alembic import op

revision = "e5a95ffb9e06"
down_revision = "a7c12a7f1462"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE workspace ADD COLUMN firehose_channel_id text "
        "REFERENCES slack_channel (id) ON DELETE SET NULL"
    )


def downgrade() -> None:
    raise NotImplementedError
