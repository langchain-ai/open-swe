"""Persist sandbox tool contexts."""

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE sandbox_tool_context (
            thread_id text PRIMARY KEY,
            configurable jsonb NOT NULL
        )
    """)


def downgrade() -> None:
    raise NotImplementedError
