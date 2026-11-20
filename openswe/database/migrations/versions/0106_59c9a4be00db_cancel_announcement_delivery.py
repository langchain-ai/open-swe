"""Cancel announcement delivery"""

from alembic import op

revision = "59c9a4be00db"
down_revision = "3d2a1cb2c1bc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE announcement ADD COLUMN cancelled_at timestamptz")


def downgrade() -> None:
    raise NotImplementedError
