"""Mirror pull request opened and merged times"""

from alembic import op

revision = "6a8e0cf46fd8"
down_revision = ["6f06bbadfc02", "3d321fd486ec", "e74e583ea10e"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE pull_request
            ADD COLUMN IF NOT EXISTS github_created_at timestamptz,
            ADD COLUMN IF NOT EXISTS merged_at timestamptz
        """
    )


def downgrade() -> None:
    raise NotImplementedError
