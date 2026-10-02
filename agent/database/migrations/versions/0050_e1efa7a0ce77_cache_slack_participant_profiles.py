"""Cache Slack participant profiles"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e1efa7a0ce77"
down_revision = ["1a27b64154a3", "52fab62a7608"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "slack_user",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    raise NotImplementedError
