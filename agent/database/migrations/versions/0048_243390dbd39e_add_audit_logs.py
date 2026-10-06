"""Add audit logs"""

from alembic import op

revision = "243390dbd39e"
down_revision = ["12b3a8ca6a0c", "c8e1ce5e9cbe", "61e8a2c88c54"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE audit_logs (
            id uuid PRIMARY KEY,
            request_time timestamptz NOT NULL,
            operation_name varchar(128) NOT NULL,
            operation_succeeded boolean,
            api_key_id text,
            user_id uuid,
            workspace_id uuid,
            enrichments jsonb NOT NULL
        )
    """)
    op.execute("CREATE INDEX audit_logs_time_idx ON audit_logs (request_time, id)")
    op.execute(
        "CREATE INDEX audit_logs_workspace_time_idx ON audit_logs (workspace_id, request_time, id)"
    )
    op.execute(
        "CREATE INDEX audit_logs_operation_time_idx ON audit_logs (operation_name, request_time, id)"
    )


def downgrade() -> None:
    raise NotImplementedError
