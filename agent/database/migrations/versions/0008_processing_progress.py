from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE deployment_metadata ADD COLUMN last_processed_at timestamptz
        """
    )

    op.execute(
        """
        UPDATE deployment_metadata SET last_processed_at = (
            SELECT max(received_at) FROM ingestion_receipts
            WHERE workspace_id = deployment_metadata.workspace_id
        )
        """
    )


def downgrade() -> None:
    raise NotImplementedError
