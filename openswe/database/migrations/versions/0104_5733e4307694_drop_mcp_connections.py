"""Drop stored MCP connections.

Runs get MCP tools only from LangSmith Managed Tools kits, so nothing reads
workspace connections (``mcp_connection``) or personal ones (``user_record``
rows of kind ``mcp_connection``). Their credentials go with them.
"""

from alembic import op

revision = "5733e4307694"
down_revision = ["1a685fe0033d", "7af88dd42073", "8ce50ebc1299"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mcp_connection")
    op.execute("DELETE FROM user_record WHERE kind = 'mcp_connection'")


def downgrade() -> None:
    raise NotImplementedError
