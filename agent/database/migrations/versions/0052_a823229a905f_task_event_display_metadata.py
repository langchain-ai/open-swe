"""Durable task messages and display metadata"""

from alembic import op

revision = "a823229a905f"
down_revision = "179eb756b3cf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE task_message (
            id uuid PRIMARY KEY,
            task_id uuid NOT NULL REFERENCES task(id) ON DELETE CASCADE,
            thread_id text NOT NULL,
            delivery_id text NOT NULL,
            content text NOT NULL,
            run_config jsonb NOT NULL,
            task_event jsonb,
            delivery_attempts integer NOT NULL DEFAULT 0,
            matched_at timestamptz NOT NULL DEFAULT now(),
            delivered_at timestamptz,
            UNIQUE (thread_id, delivery_id)
        )
    """)
    op.execute(
        "CREATE INDEX task_message_pending ON task_message(thread_id, matched_at) WHERE delivered_at IS NULL"
    )
    op.execute(
        "CREATE INDEX task_message_delivered ON task_message(delivered_at) WHERE delivered_at IS NOT NULL"
    )


def downgrade() -> None:
    raise NotImplementedError
