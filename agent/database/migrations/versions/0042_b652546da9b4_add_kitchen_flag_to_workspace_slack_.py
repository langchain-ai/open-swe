"""Add kitchen flag to workspace Slack channels

In a kitchen channel, untagged human messages start and continue agent threads.
It defaults to off, so binding a channel to a workspace never implies it.
"""

from alembic import op

revision = "b652546da9b4"
down_revision = "ec8eaeed6158"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE workspace_slack_channel
        ADD COLUMN kitchen boolean NOT NULL DEFAULT false
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE workspace_slack_channel DROP COLUMN kitchen")
