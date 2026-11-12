"""Record cached input tokens per run"""

from alembic import op

revision = "3047efb4bf0d"
down_revision = ["e17463737f00", "46f45743d6b7", "b573b549a329", "ef63546d1494"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE run_projection ADD COLUMN IF NOT EXISTS cache_read_tokens bigint, "
        "ADD COLUMN IF NOT EXISTS cache_creation_tokens bigint"
    )


def downgrade() -> None:
    raise NotImplementedError
