"""Remember the expedited card's diff image"""

from alembic import op

revision = "0a3a4d007239"
down_revision = "003f877e956f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE expedited_approval ADD COLUMN slack_diff_file_id text NOT NULL DEFAULT ''"
    )


def downgrade() -> None:
    raise NotImplementedError
