"""Add channel memory file"""

import sqlalchemy as sa
from alembic import op

revision = "04fa7beab9ec"
down_revision = ["1a27b64154a3", "243390dbd39e", "f7b59c5091a8", "52fab62a7608"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "slack_channel", sa.Column("memory", sa.Text(), nullable=False, server_default="")
    )
    op.add_column(
        "slack_channel",
        sa.Column("memory_revision", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("slack_channel", "memory_revision")
    op.drop_column("slack_channel", "memory")
