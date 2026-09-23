"""Record reviewer cost reporting cutover"""

from alembic import op

revision = "d4ab5cc1346a"
down_revision = "d742a3ec9c1b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE deployment_metadata ADD COLUMN reviewer_cost_cutover_at timestamptz")


def downgrade() -> None:
    raise NotImplementedError
