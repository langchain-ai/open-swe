"""Slack threads, runs, and messages moved out of the LangGraph Store.

``slack_thread`` is one row per Slack thread: the Open SWE thread bound to it
(or, once detached, the nonce its next thread is named with), who its untagged
messages route from, and the kickoff message its next reply replaces.
``slack_run`` is one row per run started for or posting in a Slack thread; the
latest ``started_at`` is the thread's current run, and the ``reply_*`` columns
count the run's replies held back as stale. ``slack_message`` is one row per
Slack message Open SWE tracks: the run behind a bot message, the channel thread
a bot DM was sent for, and each person's feedback reactions on it.
``slack_view_suggestion`` holds the options a code-channel view's select menus
offer.

Kickoffs and reply progress matter only while a run is going: they expire, and
writes clear a bounded batch of expired ones. ``copied_from_store`` marks a row
the Store import wrote that no write has replaced since. Bot bookkeeping writes
these rows, so ``created_by`` and ``updated_by`` are unset.

Every channel these rows name has a ``slack_channel`` row: writers add a stale
stub for a channel the directory has not looked up yet, which fills in from
Slack on first use. Nothing deletes directory rows, so the references keep the
default ``ON DELETE NO ACTION``.

The rows of ``slack_thread_mapping``, ``slack_run_mapping`` and
``slack_message_mapping`` move into the new tables with their ids, timestamps
and marks, and those tables go. A rebound thread there could keep the nonce
of an earlier detach next to its thread id; only an unbound one keeps it here.
"""

from alembic import op

revision = "1cdf9e884dad"
down_revision = ["30ab9d51b9ec", "47544c5c96d8"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE slack_thread (
            id uuid PRIMARY KEY,
            channel_id text NOT NULL REFERENCES slack_channel (id),
            thread_ts text NOT NULL,
            thread_id text,
            detach_nonce text,
            solo_user_id text,
            solo_last_seen double precision,
            solo_blocked boolean NOT NULL DEFAULT false,
            kickoff_ts text,
            kickoff_expires_at timestamptz,
            copied_from_store boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL,
            CHECK (thread_id IS NULL OR detach_nonce IS NULL),
            CHECK ((solo_user_id IS NULL) = (solo_last_seen IS NULL)),
            CHECK (NOT (solo_blocked AND solo_user_id IS NOT NULL)),
            CHECK (kickoff_ts IS NULL OR kickoff_expires_at IS NOT NULL)
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX slack_thread_location ON slack_thread (channel_id, thread_ts)
    """)
    op.execute("""
        CREATE INDEX slack_thread_kickoff_expiry ON slack_thread (kickoff_expires_at)
        WHERE kickoff_expires_at IS NOT NULL
    """)
    op.execute("""
        CREATE TABLE slack_run (
            id uuid PRIMARY KEY,
            channel_id text NOT NULL REFERENCES slack_channel (id),
            run_id text NOT NULL,
            thread_ts text NOT NULL,
            message_ts text,
            trace_message_ts text,
            triggering_user_id text,
            agent_thread_id text,
            started_at timestamptz,
            reply_seen_ts text[],
            reply_conflicts integer,
            reply_expires_at timestamptz,
            copied_from_store boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL,
            CHECK ((reply_expires_at IS NULL) = (reply_seen_ts IS NULL)),
            CHECK ((reply_expires_at IS NULL) = (reply_conflicts IS NULL))
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX slack_run_run ON slack_run (channel_id, run_id)
    """)
    op.execute("""
        CREATE INDEX slack_run_thread ON slack_run (channel_id, thread_ts, started_at DESC)
    """)
    op.execute("""
        CREATE INDEX slack_run_reply_expiry ON slack_run (reply_expires_at)
        WHERE reply_expires_at IS NOT NULL
    """)
    op.execute("""
        CREATE TABLE slack_message (
            id uuid PRIMARY KEY,
            channel_id text NOT NULL REFERENCES slack_channel (id),
            message_ts text NOT NULL,
            thread_ts text,
            run_id text,
            triggering_user_id text,
            agent_thread_id text,
            origin_channel_id text REFERENCES slack_channel (id),
            origin_thread_ts text,
            origin_subject text,
            reactions jsonb NOT NULL DEFAULT '{}',
            copied_from_store boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL,
            CHECK ((run_id IS NULL) = (thread_ts IS NULL)),
            CHECK ((origin_channel_id IS NULL) = (origin_thread_ts IS NULL)),
            CHECK ((origin_channel_id IS NULL) = (origin_subject IS NULL))
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX slack_message_message ON slack_message (channel_id, message_ts)
    """)
    op.execute("""
        CREATE INDEX slack_message_thread ON slack_message (channel_id, thread_ts)
    """)
    op.execute("""
        CREATE TABLE slack_view_suggestion (
            id uuid PRIMARY KEY,
            channel_id text NOT NULL REFERENCES slack_channel (id),
            view_id text NOT NULL,
            actions jsonb NOT NULL,
            copied_from_store boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX slack_view_suggestion_view ON slack_view_suggestion (channel_id, view_id)
    """)

    op.execute("""
        INSERT INTO slack_channel (id, fetched_at)
        SELECT channel_id, 'epoch'::timestamptz
        FROM (
            SELECT channel_id FROM slack_thread_mapping
            UNION SELECT channel_id FROM slack_run_mapping
            UNION SELECT channel_id FROM slack_message_mapping
        ) AS referenced
        ON CONFLICT (id) DO NOTHING
    """)
    op.execute("""
        INSERT INTO slack_thread (
            id, channel_id, thread_ts, thread_id, detach_nonce, copied_from_store,
            created_at, created_by, updated_at, updated_by
        )
        SELECT
            id, channel_id, thread_ts, thread_id,
            CASE WHEN thread_id IS NULL THEN detach_nonce END,
            copied_from_store, created_at, created_by, updated_at, updated_by
        FROM slack_thread_mapping
    """)
    op.execute("""
        INSERT INTO slack_run (
            id, channel_id, run_id, thread_ts, message_ts, trace_message_ts,
            triggering_user_id, agent_thread_id, started_at, copied_from_store,
            created_at, created_by, updated_at, updated_by
        )
        SELECT
            id, channel_id, run_id, thread_ts, message_ts, trace_message_ts,
            triggering_user_id, agent_thread_id, started_at, copied_from_store,
            created_at, created_by, updated_at, updated_by
        FROM slack_run_mapping
    """)
    op.execute("""
        INSERT INTO slack_message (
            id, channel_id, message_ts, thread_ts, run_id, triggering_user_id,
            agent_thread_id, copied_from_store, created_at, created_by, updated_at, updated_by
        )
        SELECT
            id, channel_id, message_ts, thread_ts, run_id, triggering_user_id,
            agent_thread_id, copied_from_store, created_at, created_by, updated_at, updated_by
        FROM slack_message_mapping
    """)
    op.execute("DROP TABLE slack_thread_mapping, slack_run_mapping, slack_message_mapping")


def downgrade() -> None:
    raise NotImplementedError
