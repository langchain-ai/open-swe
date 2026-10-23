"""Record the LangSmith API each workspace snapshot lives at"""

from alembic import op

revision = "da2e20c061b6"
# Also joins the two heads main left: merge queue configuration and Claude Code sync tokens.
down_revision = ("a887e76ff00e", "b48266ec5989")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NULL is a snapshot captured before this column, in the deployment's own LangSmith.
    op.execute("ALTER TABLE workspace ADD COLUMN IF NOT EXISTS snapshot_api text")
    # Kept for boxes still in the old region once captures move to the new sandbox region.
    op.execute("ALTER TABLE workspace ADD COLUMN IF NOT EXISTS old_region_snapshot_id text")


def downgrade() -> None:
    raise NotImplementedError
