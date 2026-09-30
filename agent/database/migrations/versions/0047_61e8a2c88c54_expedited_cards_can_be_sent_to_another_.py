"""Expedited cards can be sent to another channel"""

from alembic import op

revision = "61e8a2c88c54"
down_revision = "97c51963aefb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE human_review_request
        ADD COLUMN slack_channel_choices jsonb NOT NULL DEFAULT '[]'::jsonb,
        ADD COLUMN slack_copy_channel_id text NOT NULL DEFAULT '',
        ADD COLUMN slack_copy_ts text NOT NULL DEFAULT ''
        """
    )


def downgrade() -> None:
    raise NotImplementedError
