"""Workspace-scoped API keys."""

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE api_key (
            id text PRIMARY KEY,
            workspace text NOT NULL,
            name text NOT NULL,
            key_hash text NOT NULL UNIQUE,
            key_suffix text NOT NULL,
            created_by text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            expires_at timestamptz NOT NULL,
            last_used_at timestamptz,
            revoked_at timestamptz
        )
    """)
    op.execute("CREATE INDEX api_key_workspace_idx ON api_key (workspace)")


def downgrade() -> None:
    raise NotImplementedError
