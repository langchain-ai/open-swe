"""Count undelivered automation runs"""

from alembic import op

revision = "77c486984f68"
down_revision = "0c21542d8929"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE automation ADD COLUMN undelivered_runs integer NOT NULL DEFAULT 0")


def downgrade() -> None:
    raise NotImplementedError
