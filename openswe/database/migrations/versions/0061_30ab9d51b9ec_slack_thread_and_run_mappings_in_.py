"""Slack thread and run mappings moved out of the LangGraph Store.

``slack_thread_mapping`` binds a Slack thread to its Open SWE thread, or holds
the nonce a detached one is resolved with next. ``slack_run_mapping`` holds the
runs started for each Slack thread, the latest ``started_at`` being the thread's
current run, and ``slack_message_mapping`` the run behind each bot message.
``copied_from_store`` marks a row the Store import wrote that no write has
replaced since. The bot's own bookkeeping writes these rows, so ``created_by``
and ``updated_by`` are set only when a person's action caused the write.
"""

from alembic import op

revision = "30ab9d51b9ec"
down_revision = ["2cd871a2607d", "fcab215aad3e", "bdc6cc9ba2a9"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE slack_thread_mapping (
            id uuid PRIMARY KEY,
            channel_id text NOT NULL,
            thread_ts text NOT NULL,
            thread_id text,
            detach_nonce text,
            copied_from_store boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL,
            CHECK (thread_id IS NOT NULL OR detach_nonce IS NOT NULL)
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX slack_thread_mapping_location
        ON slack_thread_mapping (channel_id, thread_ts)
    """)
    op.execute("""
        CREATE TABLE slack_run_mapping (
            id uuid PRIMARY KEY,
            channel_id text NOT NULL,
            run_id text NOT NULL,
            thread_ts text NOT NULL,
            message_ts text,
            triggering_user_id text,
            trace_message_ts text,
            agent_thread_id text,
            started_at timestamptz,
            copied_from_store boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX slack_run_mapping_run ON slack_run_mapping (channel_id, run_id)
    """)
    op.execute("""
        CREATE INDEX slack_run_mapping_thread
        ON slack_run_mapping (channel_id, thread_ts, started_at DESC)
    """)
    op.execute("""
        CREATE TABLE slack_message_mapping (
            id uuid PRIMARY KEY,
            channel_id text NOT NULL,
            message_ts text NOT NULL,
            run_id text NOT NULL,
            thread_ts text NOT NULL,
            triggering_user_id text,
            agent_thread_id text,
            copied_from_store boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX slack_message_mapping_message
        ON slack_message_mapping (channel_id, message_ts)
    """)
    op.execute("""
        CREATE INDEX slack_message_mapping_thread ON slack_message_mapping (channel_id, thread_ts)
    """)


def downgrade() -> None:
    raise NotImplementedError
