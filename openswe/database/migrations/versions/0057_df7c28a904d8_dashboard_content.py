"""Dashboard content moved out of the LangGraph Store.

Published plans with their review comments, pinned threads, and thread
feedback prompts. A plan's ``value`` keeps the Store record's shape;
``dismissed`` hides its inline preview until the next revision is published.
"""

from alembic import op

revision = "df7c28a904d8"
down_revision = ["9ec28aa0b2a8", "b438344a06b7", "da651a8f67cc"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE thread_plan (
            thread_id text PRIMARY KEY,
            value jsonb NOT NULL,
            dismissed boolean NOT NULL DEFAULT false,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("""
        CREATE TABLE thread_plan_comment (
            thread_id text NOT NULL,
            id text NOT NULL,
            author text NOT NULL,
            author_login text NOT NULL,
            body text NOT NULL,
            anchor jsonb,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (thread_id, id)
        )
    """)
    op.execute("""
        CREATE TABLE thread_pin (
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            thread_id text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (user_id, thread_id)
        )
    """)
    op.execute("""
        CREATE TABLE thread_feedback (
            thread_id text PRIMARY KEY,
            value jsonb NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)


def downgrade() -> None:
    raise NotImplementedError
