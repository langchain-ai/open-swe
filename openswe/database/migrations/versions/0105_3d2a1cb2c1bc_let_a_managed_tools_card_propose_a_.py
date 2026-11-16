"""Let a managed tools card propose a server.

A card can hold a personal MCP server the agent proposed, which is registered in
LangSmith only once the card's owner confirms it.
"""

from alembic import op

revision = "3d2a1cb2c1bc"
down_revision = "5733e4307694"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE managed_tools_card ADD COLUMN proposed_server jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE managed_tools_card DROP COLUMN proposed_server")
