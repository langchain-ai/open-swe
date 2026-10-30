"""Add ask channel mode"""

from alembic import op

revision = "ee3ff9313927"
down_revision = "e0605414666c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE workspace_slack_channel ADD COLUMN ask boolean NOT NULL DEFAULT false")
    op.execute(
        "ALTER TABLE workspace_slack_channel ADD CONSTRAINT channel_mode_exclusive CHECK (NOT (kitchen AND ask))"
    )


def downgrade() -> None:
    raise NotImplementedError
