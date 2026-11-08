"""Track a goal's completion check"""

from alembic import op

revision = "ef63546d1494"
down_revision = ["bafee27672d1", "71279468cbb4", "4a112f9ccfc9"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE task ADD COLUMN goal_check text")


def downgrade() -> None:
    raise NotImplementedError
