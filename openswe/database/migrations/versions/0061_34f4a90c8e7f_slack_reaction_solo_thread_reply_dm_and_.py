"""Slack reaction, solo-thread, reply, DM origin and view-suggestion state moved out of the LangGraph Store.

Each table has one row per Slack location, unique on it. ``slack_solo_thread``
without a ``user_id`` and ``slack_kickoff`` without a ``kickoff_ts`` are
tombstones: the thread stopped routing untagged messages, or its kickoff was
removed. Kickoffs and reply progress expire once their run must be over.
``copied_from_store`` marks view suggestions the Store import wrote that no
write has replaced since. Reaction event dedupe uses ``event_claim``.
"""

from alembic import op

revision = "34f4a90c8e7f"
down_revision = ["2cd871a2607d", "fcab215aad3e", "bdc6cc9ba2a9"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE slack_reaction_state (
            id uuid PRIMARY KEY,
            channel_id text NOT NULL,
            message_ts text NOT NULL,
            user_id text NOT NULL,
            reactions text[] NOT NULL DEFAULT '{}',
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX slack_reaction_state_reactor
        ON slack_reaction_state (channel_id, message_ts, user_id)
    """)
    op.execute("""
        CREATE TABLE slack_solo_thread (
            id uuid PRIMARY KEY,
            channel_id text NOT NULL,
            thread_ts text NOT NULL,
            user_id text,
            last_seen double precision,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            CHECK ((user_id IS NULL) = (last_seen IS NULL))
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX slack_solo_thread_location
        ON slack_solo_thread (channel_id, thread_ts)
    """)
    op.execute("""
        CREATE TABLE slack_kickoff (
            id uuid PRIMARY KEY,
            channel_id text NOT NULL,
            thread_ts text NOT NULL,
            kickoff_ts text,
            expires_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX slack_kickoff_location ON slack_kickoff (channel_id, thread_ts)"
    )
    op.execute("CREATE INDEX slack_kickoff_expires ON slack_kickoff (expires_at)")
    op.execute("""
        CREATE TABLE slack_reply_freshness (
            id uuid PRIMARY KEY,
            run_id text NOT NULL,
            seen_ts text[] NOT NULL DEFAULT '{}',
            conflicts integer NOT NULL DEFAULT 0,
            expires_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("CREATE UNIQUE INDEX slack_reply_freshness_run ON slack_reply_freshness (run_id)")
    op.execute("CREATE INDEX slack_reply_freshness_expires ON slack_reply_freshness (expires_at)")
    op.execute("""
        CREATE TABLE slack_dm_origin (
            id uuid PRIMARY KEY,
            dm_channel_id text NOT NULL,
            message_ts text NOT NULL,
            channel_id text NOT NULL,
            thread_ts text NOT NULL,
            subject text NOT NULL DEFAULT '',
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX slack_dm_origin_message ON slack_dm_origin (dm_channel_id, message_ts)
    """)
    op.execute("""
        CREATE TABLE slack_view_suggestions (
            id uuid PRIMARY KEY,
            channel_id text NOT NULL,
            view_id text NOT NULL,
            actions jsonb NOT NULL,
            copied_from_store boolean NOT NULL DEFAULT false,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX slack_view_suggestions_view
        ON slack_view_suggestions (channel_id, view_id)
    """)


def downgrade() -> None:
    raise NotImplementedError
