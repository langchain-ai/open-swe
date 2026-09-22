"""Workspace-scoped API keys.

A key is bound to its workspace's stable id, not to the slug it was minted
against: slugs are reusable, so a key that outlived its workspace would
otherwise authenticate against a later workspace created under the same name,
with that one's repository access. The cascade is what invalidates the keys.
"""

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE api_key (
            id text PRIMARY KEY,
            workspace_id uuid NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
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
    op.execute("CREATE INDEX api_key_workspace_id_idx ON api_key (workspace_id)")
    op.execute("CREATE INDEX api_key_workspace_idx ON api_key (workspace)")


def downgrade() -> None:
    raise NotImplementedError
