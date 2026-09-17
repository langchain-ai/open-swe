from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE slack_continuation (
            id uuid PRIMARY KEY,
            thread_id text NOT NULL,
            action_id text NOT NULL,
            element_type text NOT NULL,
            label text NOT NULL DEFAULT '',
            channel_id text NOT NULL,
            thread_ts text NOT NULL DEFAULT '',
            message_ts text NOT NULL DEFAULT '',
            run_config jsonb NOT NULL DEFAULT '{}'::jsonb,
            single_use boolean NOT NULL DEFAULT true,
            state text NOT NULL CHECK (state IN ('open', 'used', 'revoked')),
            used_by text NOT NULL DEFAULT '',
            used_at timestamptz,
            expires_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    # Claiming one single-use element closes its siblings, which is a lookup by
    # the message they were posted on.
    op.execute(
        """
        CREATE INDEX slack_continuation_message_idx
            ON slack_continuation (channel_id, message_ts)
            WHERE state = 'open'
        """
    )
    op.execute("CREATE INDEX slack_continuation_thread_idx ON slack_continuation (thread_id)")
    op.execute(
        """
        CREATE INDEX slack_continuation_expiry_idx
            ON slack_continuation (expires_at)
            WHERE state = 'open'
        """
    )


def downgrade() -> None:
    raise NotImplementedError
