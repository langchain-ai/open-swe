"""Add workspace workflow edit policy"""

import sqlalchemy as sa
from alembic import op

revision = "a680a0585fdb"
down_revision = "4fd72cf43b71"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace",
        sa.Column("allow_workflow_edits", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    raise NotImplementedError
