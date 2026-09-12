from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

SQL = (
    "ALTER TABLE deployment_metadata ADD COLUMN last_processed_at timestamptz",
    "UPDATE deployment_metadata SET last_processed_at = (\n    SELECT max(received_at) FROM ingestion_receipts\n    WHERE workspace_id = deployment_metadata.workspace_id\n)",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError
