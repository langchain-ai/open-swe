from agent.analytics.migrations.operations import execute_script

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

SQL = "SET search_path TO open_swe, public;\n\nALTER TABLE deployment_metadata ADD COLUMN last_processed_at timestamptz;\n\nUPDATE deployment_metadata SET last_processed_at = (\n    SELECT max(received_at) FROM ingestion_receipts\n    WHERE workspace_id = deployment_metadata.workspace_id\n);\n"


def upgrade() -> None:
    execute_script(SQL)


def downgrade() -> None:
    raise NotImplementedError
