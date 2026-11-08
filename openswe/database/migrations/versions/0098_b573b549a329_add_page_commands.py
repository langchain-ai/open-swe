"""Add page commands

``page_command`` is one command Robin queued for a dashboard page to carry out, such
as bringing code into view. Each browser tab that sends a message with its page's
tools is a page session; its commands run in ``position`` order, and the page records
each one's result once it has run it.
"""

from alembic import op

revision = "b573b549a329"
down_revision = ["bafee27672d1", "71279468cbb4", "4a112f9ccfc9"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS page_command (
            id uuid PRIMARY KEY,
            owner uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            page_session uuid NOT NULL,
            position integer NOT NULL,
            thread_id text NOT NULL,
            tool text NOT NULL,
            arguments jsonb NOT NULL,
            executed_at timestamptz,
            result jsonb,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS page_command_position"
        " ON page_command (owner, page_session, position)"
    )


def downgrade() -> None:
    raise NotImplementedError
