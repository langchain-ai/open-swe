"""Repair missing task messages from preview drafts"""

from alembic import op

revision = "7d34d8e5b6a3"
down_revision = ["6f06bbadfc02", "a823229a905f"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS task_message (
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
        "CREATE INDEX IF NOT EXISTS task_message_pending "
        "ON task_message(thread_id, matched_at) WHERE delivered_at IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS task_message_delivered "
        "ON task_message(delivered_at) WHERE delivered_at IS NOT NULL"
    )


def downgrade() -> None:
    raise NotImplementedError
