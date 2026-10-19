"""Slacklets"""

from alembic import op

revision = "a83909357d9f"
down_revision = "9f867d4517fa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE slacklet (
            id uuid PRIMARY KEY,
            kind text NOT NULL,
            subject text NOT NULL,
            slack_thread_id uuid NOT NULL REFERENCES slack_thread (id) ON DELETE CASCADE,
            message_ts text NOT NULL,
            state jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX slacklet_kind_subject_thread_idx "
        "ON slacklet (kind, subject text_pattern_ops, slack_thread_id)"
    )
    op.execute("CREATE INDEX slacklet_slack_thread_idx ON slacklet (slack_thread_id)")
    op.execute(
        "COMMENT ON TABLE slacklet IS "
        "'A live Slack message of some kind, about subject; its state changes only by commands.'"
    )
    op.execute(
        """
        CREATE TABLE slacklet_definition (
            id uuid PRIMARY KEY,
            name text NOT NULL,
            source text NOT NULL,
            source_sha256 text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX slacklet_definition_source_idx ON slacklet_definition (source_sha256)"
    )
    op.execute(
        "COMMENT ON TABLE slacklet_definition IS "
        "'Agent-written slacklet programs; a dynamic slacklet''s subject is one of these ids.'"
    )
    op.execute(
        """
        CREATE TABLE slacklet_command (
            id uuid PRIMARY KEY,
            slacklet_id uuid NOT NULL REFERENCES slacklet (id) ON DELETE CASCADE,
            seq integer NOT NULL,
            command jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX slacklet_command_seq_idx ON slacklet_command (slacklet_id, seq)"
    )
    op.execute(
        "COMMENT ON TABLE slacklet_command IS "
        "'Append-only typed commands; replaying a slacklet''s from seq 1 rebuilds its state.'"
    )


def downgrade() -> None:
    raise NotImplementedError
