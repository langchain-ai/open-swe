"""Cache branch required approving reviews"""

from alembic import op

revision = "cff3e5d43f43"
down_revision = "3047efb4bf0d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE branch_config ADD COLUMN IF NOT EXISTS required_approving_reviews integer"
    )


def downgrade() -> None:
    raise NotImplementedError
