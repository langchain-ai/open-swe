"""Managed tools connect cards moved out of the LangGraph Store.

One row per card the agent offered a person in a thread. Cards still in the
Store are not copied: a click on one finds no card and fails closed, and the
person asks the agent for a new one.
"""

from alembic import op

revision = "bdc6cc9ba2a9"
down_revision = "b69cf766defc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE managed_tools_card (
            thread_id text NOT NULL,
            card_id text NOT NULL,
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            gateway text NOT NULL,
            services text[] NOT NULL DEFAULT '{}',
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (thread_id, card_id)
        )
    """)


def downgrade() -> None:
    raise NotImplementedError
