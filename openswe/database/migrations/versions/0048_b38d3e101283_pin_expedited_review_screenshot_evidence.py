"""Pin expedited review screenshot evidence"""

from alembic import op

revision = "b38d3e101283"
down_revision = "61e8a2c88c54"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE human_review_request ADD COLUMN screenshot_body text NOT NULL DEFAULT ''"
    )


def downgrade() -> None:
    raise NotImplementedError
