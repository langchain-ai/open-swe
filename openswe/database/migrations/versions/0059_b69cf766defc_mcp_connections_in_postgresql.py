"""MCP connections moved out of the LangGraph Store.

``mcp_connection`` holds each workspace's connections under its slug, which,
as in ``settings_record``, may come before the workspace row does, and the
instance tier's without one. ``connection`` keeps the Store record's shape.
Personal connections are ``user_record`` rows of kind ``mcp_connection``.
"""

from alembic import op

revision = "b69cf766defc"
down_revision = "1563ad8bd725"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE mcp_connection (
            id uuid PRIMARY KEY,
            workspace text,
            name text NOT NULL,
            connection jsonb NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX mcp_connection_workspace_name ON mcp_connection (workspace, name)
        WHERE workspace IS NOT NULL
    """)
    op.execute("""
        CREATE UNIQUE INDEX mcp_connection_instance_name ON mcp_connection (name)
        WHERE workspace IS NULL
    """)


def downgrade() -> None:
    raise NotImplementedError
