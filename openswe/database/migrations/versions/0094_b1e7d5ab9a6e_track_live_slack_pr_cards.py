"""Track live Slack PR cards.

Each pull request card Open SWE posts in a Slack message is redrawn from its pull
request's webhooks until ``live_until``.
"""

from alembic import op

revision = "b1e7d5ab9a6e"
down_revision = ["c4f9b8528cee", "53b42abdbed7"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        """
        CREATE TABLE slack_pull_request_card (
            id uuid PRIMARY KEY,
            pull_request_id uuid NOT NULL REFERENCES pull_request (id) ON DELETE CASCADE,
            channel_id text NOT NULL REFERENCES slack_channel (id),
            thread_ts text NOT NULL,
            message_ts text NOT NULL,
            live_until timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX slack_pull_request_card_message "
        "ON slack_pull_request_card (channel_id, message_ts, pull_request_id)"
    )
    op.execute(
        "CREATE INDEX slack_pull_request_card_live "
        "ON slack_pull_request_card (pull_request_id, live_until)"
    )


def downgrade() -> None:
    raise NotImplementedError
