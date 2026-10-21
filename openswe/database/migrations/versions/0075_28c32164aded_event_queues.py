"""Event queues"""

from alembic import op

revision = "28c32164aded"
down_revision = "87e4182be853"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE event_queue (
            id uuid PRIMARY KEY,
            expires_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX event_queue_created_by_idx ON event_queue (created_by)")
    op.execute(
        """
        CREATE TABLE event_queue_thread (
            id uuid PRIMARY KEY,
            queue_id uuid NOT NULL REFERENCES event_queue (id) ON DELETE CASCADE,
            thread_id text NOT NULL REFERENCES thread (thread_id) ON DELETE CASCADE,
            after_version bigint NOT NULL,
            event_types text[] NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX event_queue_thread_key ON event_queue_thread (queue_id, thread_id)"
    )
    op.execute("CREATE INDEX event_queue_thread_thread_idx ON event_queue_thread (thread_id)")
    for statement in (
        "COMMENT ON TABLE event_queue IS 'A caller outside any thread, such as a remote MCP "
        "client, following thread transcripts: each long poll drains what its threads logged "
        "since the last one.'",
        "COMMENT ON COLUMN event_queue.expires_at IS 'Pushed forward by every subscribe and "
        "drain; an expired queue is deleted the next time any queue is created.'",
        "COMMENT ON COLUMN event_queue.created_by IS 'The only person who may drain or extend "
        "the queue.'",
        "COMMENT ON TABLE event_queue_thread IS 'One thread an event_queue follows.'",
        "COMMENT ON COLUMN event_queue_thread.after_version IS 'The thread_event version the "
        "queue has drained through.'",
        "COMMENT ON COLUMN event_queue_thread.event_types IS 'thread_event.event_type values "
        "the queue hands out; the others are skipped.'",
    ):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE event_queue_thread")
    op.execute("DROP TABLE event_queue")
