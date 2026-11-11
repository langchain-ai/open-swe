"""Keep the Slack search grant each message carries.

Slack sends a channel mention as both ``message`` and ``app_mention``, and only
``app_mention`` carries the ``action_token`` that Slack search requires. Whichever
event arrives first starts the run, so the run looks the grant up by its message.
"""

from alembic import op

revision = "e6cdee04906b"
down_revision = "3047efb4bf0d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""
        CREATE TABLE slack_search_grant (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL,
            channel_id text NOT NULL REFERENCES slack_channel (id),
            message_ts text NOT NULL,
            action_token text NOT NULL
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX slack_search_grant_message "
        "ON slack_search_grant (channel_id, message_ts)"
    )
    op.execute("CREATE INDEX slack_search_grant_created_at ON slack_search_grant (created_at)")
    op.execute(
        "COMMENT ON TABLE slack_search_grant IS 'The search grant Slack attached to a "
        "message, for the run that message starts; lapses an hour after created_at.'"
    )
    op.execute(
        "COMMENT ON COLUMN slack_search_grant.action_token IS 'Slack''s action_token, "
        "encrypted under TOKEN_ENCRYPTION_KEY.'"
    )


def downgrade() -> None:
    raise NotImplementedError
