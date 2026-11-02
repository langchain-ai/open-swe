"""List Slack channels from the channel directory"""

from alembic import op

revision = "9553a7e0a493"
down_revision = ["b48266ec5989", "a887e76ff00e"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE slack_channel ADD COLUMN listed boolean NOT NULL DEFAULT false")
    # Channels already looked up are listed until the first walk over Slack confirms them.
    op.execute(
        """
        UPDATE slack_channel SET listed = true
        WHERE name <> ''
            AND payload->>'is_archived' IS DISTINCT FROM 'true'
            AND payload->>'is_im' IS DISTINCT FROM 'true'
            AND payload->>'is_mpim' IS DISTINCT FROM 'true'
        """
    )
    op.execute("CREATE INDEX slack_channel_listed_name_idx ON slack_channel (name) WHERE listed")


def downgrade() -> None:
    raise NotImplementedError
