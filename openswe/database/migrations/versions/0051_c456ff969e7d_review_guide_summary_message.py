"""The review guide's own Slack messages: its progress summary and its pause note."""

from alembic import op

revision = "c456ff969e7d"
down_revision = "6abbf75d3103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The summary message is what Slack shows in the thread the channel came from.
    op.execute(
        "ALTER TABLE review_guide_session ADD COLUMN summary_message_ts text NOT NULL DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE review_guide_session ADD COLUMN paused_message_ts text NOT NULL DEFAULT ''"
    )


def downgrade() -> None:
    raise NotImplementedError
