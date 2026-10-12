"""Agent Skills and offloaded thread blobs, moved out of the LangGraph Store.

A skill with no ``user_id`` belongs to the organization. Values keep the
deepagents file shape (``content`` and ``encoding``) the agent reads them in.
"""

from alembic import op

revision = "b58096f6b755"
down_revision = "e74e583ea10e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE skill (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            user_id uuid REFERENCES users (id) ON DELETE CASCADE,
            name text NOT NULL,
            value jsonb NOT NULL
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX skill_owner_name
        ON skill (COALESCE(user_id, '00000000-0000-0000-0000-000000000000'), name)
    """)
    op.execute("""
        CREATE TABLE thread_blob (
            thread_id text NOT NULL,
            path text NOT NULL,
            value jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (thread_id, path)
        )
    """)


def downgrade() -> None:
    raise NotImplementedError
