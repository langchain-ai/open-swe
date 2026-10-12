"""Restore the human review card's diff image column where a reverted migration dropped it

A since-deleted migration (45ca3f99680e) dropped the column on databases that ran
it; everywhere else the column exists and this is a no-op.
"""

from alembic import op

revision = "84e4e97b6fec"
down_revision = "76d4a1e661ad"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE human_review_request "
        "ADD COLUMN IF NOT EXISTS slack_diff_file_id text NOT NULL DEFAULT ''"
    )


def downgrade() -> None:
    raise NotImplementedError
