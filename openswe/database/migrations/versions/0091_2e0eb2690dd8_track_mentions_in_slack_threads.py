"""Track mentions in Slack threads.

Paired with ``slack_pull_request_link`` on the thread, a mention puts the PRs a
thread links on the mentioned person's To Review list.
"""

from alembic import op

revision = "2e0eb2690dd8"
down_revision = ["1850da6129ee", "3c9e71d2a4b8"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE slack_thread_mention (
            id uuid PRIMARY KEY,
            team_id text NOT NULL,
            channel_id text NOT NULL,
            thread_ts text NOT NULL,
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            message_ts text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX slack_thread_mention_thread_user "
        "ON slack_thread_mention (team_id, channel_id, thread_ts, user_id)"
    )
    op.execute(
        "CREATE INDEX slack_thread_mention_user ON slack_thread_mention (user_id, created_at)"
    )


def downgrade() -> None:
    raise NotImplementedError
