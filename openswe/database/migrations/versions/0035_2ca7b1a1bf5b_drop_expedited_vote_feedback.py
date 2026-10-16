"""Drop expedited vote feedback"""

from alembic import op

revision = "2ca7b1a1bf5b"
down_revision = "a9b72c4a6ad1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE expedited_approval_vote DROP COLUMN feedback")


def downgrade() -> None:
    raise NotImplementedError
