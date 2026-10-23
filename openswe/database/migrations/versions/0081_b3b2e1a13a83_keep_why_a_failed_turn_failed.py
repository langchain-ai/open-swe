"""Keep why a failed turn failed"""

from alembic import op

revision = "b3b2e1a13a83"
down_revision = "a7c12a7f1462"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # What the person reads about a failed turn, beside the technical ``error``.
    op.execute("ALTER TABLE thread_turn ADD COLUMN IF NOT EXISTS reason text")


def downgrade() -> None:
    raise NotImplementedError
