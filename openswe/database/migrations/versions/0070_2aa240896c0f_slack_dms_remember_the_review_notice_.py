"""Slack DMs remember the review notice they carried"""

from alembic import op

revision = "2aa240896c0f"
down_revision = ["ba23c09c195d", "e5e52ce891f4"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE slack_message ADD COLUMN IF NOT EXISTS origin_notice JSONB")
    op.execute(
        "COMMENT ON COLUMN slack_message.origin_notice IS 'The automated review notice a DM "
        "carried, so a reply to it reaches the agent described.'"
    )


def downgrade() -> None:
    raise NotImplementedError
