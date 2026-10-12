"""Thread replies owed for dispatched messages"""

from alembic import op

revision = "d8992d0a87dd"
down_revision = ["9ec28aa0b2a8", "b438344a06b7"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE thread_reply (
            id uuid PRIMARY KEY,
            requester_thread_id text NOT NULL,
            target_thread_id text NOT NULL,
            message_id text NOT NULL,
            requested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            answered_at timestamptz
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX thread_reply_message_idx ON thread_reply "
        "(target_thread_id, message_id)"
    )
    op.execute(
        "CREATE INDEX thread_reply_open_idx ON thread_reply (target_thread_id) "
        "WHERE answered_at IS NULL"
    )
    for statement in (
        "COMMENT ON TABLE thread_reply IS 'A reply one agent thread owes another for a message "
        "the requester sent it. The first run to end with the message in its state answers it.'",
        "COMMENT ON COLUMN thread_reply.message_id IS 'Id of the dispatched message in the "
        "target thread state.'",
        "COMMENT ON COLUMN thread_reply.answered_at IS 'When the answer was recorded as an "
        "event_match for the requester.'",
        "COMMENT ON COLUMN event_match.subscription_id IS 'The event_subscription or "
        "thread_reply that matched; no foreign key, since a one-shot is deleted as it matches.'",
    ):
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError
