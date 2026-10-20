"""Persist standard review auto merge choice"""

import sqlalchemy as sa
from alembic import op

revision = "3bed5f27edb1"
down_revision = "8cb312f3b529"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "human_review_request",
        sa.Column("auto_merge", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    raise NotImplementedError
