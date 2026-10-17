"""Pull request slacklets"""

from alembic import op

revision = "5f30dc82668b"
down_revision = "9f867d4517fa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE pull_request_slacklet (
            id uuid PRIMARY KEY,
            slack_thread_id uuid NOT NULL REFERENCES slack_thread (id) ON DELETE CASCADE,
            pull_request_id uuid NOT NULL REFERENCES pull_request (id) ON DELETE CASCADE,
            message_ts text NOT NULL,
            pull_request_view jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX pull_request_slacklet_thread_pr_idx "
        "ON pull_request_slacklet (slack_thread_id, pull_request_id)"
    )
    op.execute(
        "CREATE INDEX pull_request_slacklet_pr_idx ON pull_request_slacklet (pull_request_id)"
    )
    op.execute(
        "CREATE INDEX pull_request_slacklet_head_idx "
        "ON pull_request_slacklet ((pull_request_view -> 'ci' ->> 'sha'))"
    )
    op.execute(
        "COMMENT ON TABLE pull_request_slacklet IS "
        "'A Slack message kept in step with a pull request; its state changes only by commands.'"
    )
    op.execute(
        """
        CREATE TABLE pull_request_slacklet_command (
            id uuid PRIMARY KEY,
            slacklet_id uuid NOT NULL REFERENCES pull_request_slacklet (id) ON DELETE CASCADE,
            seq integer NOT NULL,
            command jsonb NOT NULL,
            log_message_ts text,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX pull_request_slacklet_command_seq_idx "
        "ON pull_request_slacklet_command (slacklet_id, seq)"
    )
    op.execute(
        "COMMENT ON TABLE pull_request_slacklet_command IS "
        "'Append-only typed commands; replaying a slacklet''s from seq 1 rebuilds its state.'"
    )


def downgrade() -> None:
    raise NotImplementedError
