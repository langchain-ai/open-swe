"""Remove mention-only Slack bindings"""

from alembic import op

revision = "d9d2e51c4a9e"
down_revision = "4b3643ee9665"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("ALTER TABLE integration_slack_channel ALTER COLUMN mode DROP DEFAULT")
    op.execute("DELETE FROM integration_slack_channel WHERE mode = 'mentions'")
    op.execute("""
        ALTER TABLE integration_slack_channel
            DROP CONSTRAINT integration_slack_channel_mode_check,
            ADD CONSTRAINT integration_slack_channel_mode_check CHECK (mode IN ('kitchen', 'ask'))
    """)


def downgrade() -> None:
    raise NotImplementedError
