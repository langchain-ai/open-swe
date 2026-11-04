"""Managed tools belong to people and workspace bots, and neither uses a kit.

Connect cards name a person's LMT servers, not a kit: cards offered for the old
instance kit name its credential slugs, which no longer match a server, so they go,
and a person asks the agent for a new one. Kit pins go from every settings tier: a
workspace bot now loads every server its LMT profile connected.
"""

from alembic import op

revision = "44ccfb67922d"
down_revision = "5fa6cdee053a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM managed_tools_card")
    op.execute("ALTER TABLE managed_tools_card DROP COLUMN gateway")
    op.execute("UPDATE settings_record SET value = value - 'managed_tools_gateway_id'")


def downgrade() -> None:
    raise NotImplementedError
