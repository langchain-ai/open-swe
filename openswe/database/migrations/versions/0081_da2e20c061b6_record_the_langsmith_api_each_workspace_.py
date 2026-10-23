"""Record the LangSmith API each workspace snapshot lives at"""

from alembic import op

revision = "da2e20c061b6"
down_revision = "a7c12a7f1462"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NULL is a snapshot captured before this column, in the deployment's own LangSmith.
    op.execute("ALTER TABLE workspace ADD COLUMN IF NOT EXISTS snapshot_api text")


def downgrade() -> None:
    raise NotImplementedError
