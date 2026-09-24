"""How far the thread index reconciler's walk of LangGraph has got.

``max(thread_index.synced_at)`` cannot serve as the mark: write-through and
turn events bump ``synced_at`` too, so it would run ahead of what the walk has
actually covered. The mark is LangGraph's own ``updated_at`` column, the one
the walk sorts by.
"""

from alembic import op

revision = "7911b879d8ce"
down_revision = "c8b081491303"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE thread_index_sync_state (
            id smallint PRIMARY KEY CHECK (id = 1),
            last_langgraph_updated_at timestamptz,
            last_full_sync_at timestamptz
        )
        """
    )


def downgrade() -> None:
    raise NotImplementedError
