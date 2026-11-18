"""Track managed tools Slack connection messages"""

from alembic import op

revision = "606fff7c4ea3"
down_revision = "3d2a1cb2c1bc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE managed_tools_card ADD COLUMN slack_channel_id text REFERENCES slack_channel(id) ON DELETE SET NULL"
    )
    op.execute("ALTER TABLE managed_tools_card ADD COLUMN slack_message_ts text")
    op.execute("ALTER TABLE managed_tools_card ADD COLUMN slack_footer text")


def downgrade() -> None:
    raise NotImplementedError
