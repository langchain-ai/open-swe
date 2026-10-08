"""Add API key descriptions"""

import sqlalchemy as sa
from alembic import op

revision = "12b3a8ca6a0c"
down_revision = "b652546da9b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_key", sa.Column("description", sa.Text(), nullable=True))


def downgrade() -> None:
    raise NotImplementedError
