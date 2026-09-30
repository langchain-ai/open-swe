"""Create the Slack channel directory where a colliding 0018 was stamped instead."""

from alembic import op

revision = "abb47eb3f8b2"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS slack_channel (
            id text PRIMARY KEY,
            name text NOT NULL DEFAULT '',
            payload jsonb NOT NULL DEFAULT '{}'::jsonb,
            fetched_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS slack_channel_name_idx ON slack_channel (name)")


def downgrade() -> None:
    raise NotImplementedError
