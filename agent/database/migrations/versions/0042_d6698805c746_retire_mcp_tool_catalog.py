"""Retire mcp_tool_catalog

Keeps revision d6698805c746 resolvable: databases that ran the unmerged catalog
migration are stamped with it, and alembic refuses to start without it.
"""

from alembic import op

revision = "d6698805c746"
down_revision = "ec8eaeed6158"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mcp_tool_catalog")


def downgrade() -> None:
    raise NotImplementedError
