"""Store GitHub repository ids"""

from alembic import op

revision = "e992802ae035"
down_revision = "2ca7b1a1bf5b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE repository
        ADD COLUMN github_id bigint,
        ADD COLUMN github_checked_at timestamptz
        """
    )


def downgrade() -> None:
    raise NotImplementedError
