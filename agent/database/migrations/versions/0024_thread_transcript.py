"""The append-only transcript event log, and the projections read by the UI.

``thread_event`` is the only source of truth: every other table here is a
projection of it that exists so a thread can be rendered with one round of
queries instead of a fold over the log. ``thread.version`` is the last version
appended for the thread, which is what makes the log gapless per thread and
lets a reader resume with ``version > after``.

``thread.metadata`` mirrors the LangGraph thread metadata so the read path can
authorize a caller without calling LangGraph at all.
"""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE thread (
            thread_id text PRIMARY KEY,
            version bigint NOT NULL DEFAULT 0,
            status text NOT NULL DEFAULT 'idle' CHECK (status IN ('idle', 'running', 'error')),
            title text,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE thread_event (
            thread_id text NOT NULL REFERENCES thread (thread_id) ON DELETE CASCADE,
            version bigint NOT NULL,
            event_id uuid NOT NULL UNIQUE,
            event_type text NOT NULL,
            schema_version smallint NOT NULL DEFAULT 1,
            run_id text,
            turn_id uuid,
            command_id text,
            actor_kind text NOT NULL CHECK (actor_kind IN ('user', 'agent', 'system')),
            occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            payload jsonb NOT NULL,
            PRIMARY KEY (thread_id, version)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX thread_event_run_idx ON thread_event (run_id) WHERE run_id IS NOT NULL
        """
    )

    op.execute(
        """
        CREATE TABLE thread_command_receipt (
            thread_id text NOT NULL,
            command_id text NOT NULL,
            result_version bigint,
            accepted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (thread_id, command_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE thread_turn (
            turn_id uuid PRIMARY KEY,
            thread_id text NOT NULL REFERENCES thread (thread_id) ON DELETE CASCADE,
            run_id text,
            state text NOT NULL CHECK (state IN ('requested', 'running', 'completed', 'failed', 'interrupted')),
            requested_at timestamptz NOT NULL,
            started_at timestamptz,
            completed_at timestamptz,
            error text
        )
        """
    )

    # Serves both the ascending read and the backwards keyset walk a windowed
    # read pages with: (requested_at, turn_id) < (:before_at, :before_turn)
    # ordered DESC is this index scanned in reverse.
    op.execute(
        """
        CREATE INDEX thread_turn_thread_idx ON thread_turn (thread_id, requested_at, turn_id)
        """
    )

    # The commit each turn left behind in its sandbox, so a later reader can
    # diff one turn against another without the sandbox being alive to ask.
    op.execute(
        """
        CREATE TABLE thread_turn_checkpoint (
            thread_id text NOT NULL REFERENCES thread (thread_id) ON DELETE CASCADE,
            turn_id uuid NOT NULL,
            checkpoint_turn_count int NOT NULL,
            checkpoint_ref text NOT NULL,
            commit text,
            status text NOT NULL CHECK (status IN ('ready', 'missing', 'error')),
            files jsonb NOT NULL DEFAULT '[]'::jsonb,
            assistant_message_id text,
            error text,
            completed_at timestamptz NOT NULL,
            PRIMARY KEY (thread_id, turn_id)
        )
        """
    )

    # The ordinal is the order a reader shows checkpoints in, and it is
    # assigned under the thread's lock, so two turns of one thread never share it.
    op.execute(
        """
        CREATE UNIQUE INDEX thread_turn_checkpoint_count_idx
        ON thread_turn_checkpoint (thread_id, checkpoint_turn_count)
        """
    )

    op.execute(
        """
        CREATE TABLE thread_message (
            thread_id text NOT NULL REFERENCES thread (thread_id) ON DELETE CASCADE,
            message_id text NOT NULL,
            turn_id uuid NOT NULL,
            version bigint NOT NULL,
            role text NOT NULL CHECK (role IN ('human', 'ai')),
            text text NOT NULL DEFAULT '',
            reasoning text NOT NULL DEFAULT '',
            namespace text[] NOT NULL DEFAULT '{}',
            sender jsonb,
            images jsonb,
            usage jsonb,
            created_at timestamptz NOT NULL,
            PRIMARY KEY (thread_id, message_id)
        )
        """
    )

    # A windowed read fetches the messages of one page of turns, so the index
    # is keyed by turn and then by the order they are returned in.
    op.execute(
        """
        CREATE INDEX thread_message_turn_idx
        ON thread_message (thread_id, turn_id, created_at, message_id)
        """
    )

    op.execute(
        """
        CREATE TABLE thread_tool_call (
            thread_id text NOT NULL REFERENCES thread (thread_id) ON DELETE CASCADE,
            tool_call_id text NOT NULL,
            turn_id uuid NOT NULL,
            message_id text,
            version bigint NOT NULL,
            name text NOT NULL,
            input jsonb NOT NULL,
            status text NOT NULL CHECK (status IN ('in_progress', 'completed', 'error')),
            output_preview text,
            output text,
            output_truncated boolean NOT NULL DEFAULT false,
            namespace text[] NOT NULL DEFAULT '{}',
            started_at timestamptz NOT NULL,
            ended_at timestamptz,
            PRIMARY KEY (thread_id, tool_call_id)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX thread_tool_call_turn_idx
        ON thread_tool_call (thread_id, turn_id, started_at, tool_call_id)
        """
    )

    # The snapshot serves the newest notice per kind for the newest turn, which
    # is a backwards scan over this index rather than over the whole log.
    op.execute(
        """
        CREATE INDEX thread_event_notice_idx
        ON thread_event (thread_id, turn_id, version DESC)
        WHERE event_type = 'run.notice'
        """
    )

    # Attachment bytes live outside ``thread_event`` so the log a subscriber
    # replays stays small; an event carries the ``attachment_id`` only.
    op.execute(
        """
        CREATE TABLE thread_attachment (
            attachment_id uuid PRIMARY KEY,
            thread_id text NOT NULL REFERENCES thread (thread_id) ON DELETE CASCADE,
            message_id text NOT NULL,
            position int NOT NULL,
            mime_type text NOT NULL,
            file_name text,
            data bytea NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )

    op.execute(
        """
        CREATE INDEX thread_attachment_message_idx
        ON thread_attachment (thread_id, message_id, position)
        """
    )


def downgrade() -> None:
    raise NotImplementedError
