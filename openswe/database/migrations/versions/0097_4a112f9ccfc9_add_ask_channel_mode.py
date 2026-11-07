"""Add ask channel mode"""

from alembic import op

revision = "4a112f9ccfc9"
down_revision = ["05cc16abd7bf", "11d943df8abc", "ba72445cfa9a"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE workspace_slack_channel ADD COLUMN ask boolean NOT NULL DEFAULT false")
    op.execute(
        "ALTER TABLE workspace_slack_channel ADD CONSTRAINT channel_mode_exclusive CHECK (NOT (kitchen AND ask))"
    )


def downgrade() -> None:
    raise NotImplementedError
