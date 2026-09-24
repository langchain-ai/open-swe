"""Store pull request line counts"""

from alembic import op

revision = "0a80973775d3"
down_revision = "e992802ae035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE pull_request
        ADD COLUMN additions integer,
        ADD COLUMN deletions integer,
        ADD COLUMN changed_files integer
        """
    )


def downgrade() -> None:
    raise NotImplementedError
