"""MCP tool catalog"""

from alembic import op

revision = "d6698805c746"
down_revision = "ec8eaeed6158"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE mcp_tool_catalog (
            namespace text NOT NULL,
            connection_name text NOT NULL,
            revision text NOT NULL,
            tools jsonb NOT NULL,
            fetched_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (namespace, connection_name)
        )
        """
    )
    for statement in (
        "COMMENT ON TABLE mcp_tool_catalog IS 'Tool definitions last discovered from each MCP "
        "connection, shared by every worker so a run reuses discovery instead of connecting to "
        "the server.'",
        "COMMENT ON COLUMN mcp_tool_catalog.namespace IS 'JSON array of the connection source "
        'namespace, e.g. ["user_mcps", "<login>"].\'',
        "COMMENT ON COLUMN mcp_tool_catalog.revision IS 'Connection revision the tools were "
        "discovered with; a different revision means the settings changed and the row is ignored.'",
        "COMMENT ON COLUMN mcp_tool_catalog.tools IS 'MCP Tool objects as returned by list_tools, "
        "before allowed_tools filtering.'",
        "COMMENT ON COLUMN mcp_tool_catalog.fetched_at IS 'When discovery last succeeded; rows "
        "older than the catalog TTL are served and refreshed in the background.'",
    ):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE mcp_tool_catalog")
