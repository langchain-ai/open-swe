"""MCP connections moved out of the LangGraph Store.

``mcp_connection`` holds the instance tier's connections under workspace ``''``
and each workspace's under its slug, which, as in ``settings_record``, may come
before the workspace row does. ``value`` keeps the Store record's shape.
Personal connections are ``user_record`` rows of kind ``mcp_connection``.
"""

from alembic import op

revision = "b69cf766defc"
down_revision = ["7d34d8e5b6a3", "df7c28a904d8"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE mcp_connection (
            tier text NOT NULL CHECK (tier IN ('instance', 'workspace')),
            workspace text NOT NULL,
            name text NOT NULL,
            value jsonb NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (tier, workspace, name)
        )
    """)


def downgrade() -> None:
    raise NotImplementedError
