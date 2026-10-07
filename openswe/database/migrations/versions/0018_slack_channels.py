"""Slack channel directory, replacing the in-process channel info cache."""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE slack_channel (
            id text PRIMARY KEY,
            name text NOT NULL DEFAULT '',
            payload jsonb NOT NULL DEFAULT '{}'::jsonb,
            fetched_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    op.execute("CREATE INDEX slack_channel_name_idx ON slack_channel (name)")


def downgrade() -> None:
    raise NotImplementedError
