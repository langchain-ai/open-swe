"""Slack channel config: pull request watching and standing instructions"""

from alembic import op

revision = "9b689c0f1dcb"
down_revision = "0a80973775d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE slack_channel_config (
            channel_id text PRIMARY KEY,
            watch_pull_requests boolean NOT NULL DEFAULT false,
            instructions text NOT NULL DEFAULT '',
            updated_by text NOT NULL DEFAULT '',
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE slack_watched_pull_request (
            channel_id text NOT NULL,
            message_ts text NOT NULL,
            owner text NOT NULL,
            repo text NOT NULL,
            number integer NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (owner, repo, number, channel_id, message_ts)
        )
        """
    )


def downgrade() -> None:
    raise NotImplementedError
