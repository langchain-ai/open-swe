"""Managed tools connect cards name a person's LMT servers, not a kit.

Cards offered for the old instance kit name its credential slugs, which no longer
match a server; a click on one fails closed and the person asks for a new card.
"""

from alembic import op

revision = "44ccfb67922d"
down_revision = "5fa6cdee053a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM managed_tools_card")
    op.execute("ALTER TABLE managed_tools_card DROP COLUMN gateway")


def downgrade() -> None:
    raise NotImplementedError
