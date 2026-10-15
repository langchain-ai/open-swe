"""Personal hosted MCP OAuth authorizations"""

from alembic import op

revision = "923cc20d4471"
down_revision = "c8d8a5c65df3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE personal_mcp_oauth (
            id uuid PRIMARY KEY,
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            name text NOT NULL,
            sealed_authorization text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX personal_mcp_oauth_owner_name ON personal_mcp_oauth (user_id, name)"
    )


def downgrade() -> None:
    raise NotImplementedError
