from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE deployment_metadata
            ADD COLUMN IF NOT EXISTS reporting_cutover_at timestamptz
        """
    )


def downgrade() -> None:
    raise NotImplementedError
