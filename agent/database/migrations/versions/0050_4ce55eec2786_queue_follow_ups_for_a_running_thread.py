"""Queue follow-ups for a running thread"""

from alembic import op

revision = "4ce55eec2786"
down_revision = ["1a27b64154a3", "243390dbd39e", "f7b59c5091a8", "52fab62a7608"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE thread_queued_message (
            seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            thread_id text NOT NULL,
            queue_id text,
            content jsonb NOT NULL,
            queued_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    op.execute(
        "CREATE INDEX thread_queued_message_thread_idx ON thread_queued_message (thread_id, seq)"
    )
    op.execute(
        """
        CREATE UNIQUE INDEX thread_queued_message_queue_id_idx
        ON thread_queued_message (thread_id, queue_id) WHERE queue_id IS NOT NULL
        """
    )


def downgrade() -> None:
    raise NotImplementedError
