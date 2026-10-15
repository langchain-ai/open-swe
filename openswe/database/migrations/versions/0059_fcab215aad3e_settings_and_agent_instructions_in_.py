"""Instance and workspace settings and per-repository agent instructions, moved out of the LangGraph Store.

``settings_record`` holds one JSON document per tier and key: the instance
record under ``('instance', '')`` and each workspace's sparse overrides under
``('workspace', <slug>)``. Workspace overrides key on the slug rather than the
workspace row because the default workspace's overrides may exist before it does.
"""

from alembic import op

revision = "fcab215aad3e"
down_revision = "1563ad8bd725"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE settings_record (
            tier text NOT NULL CHECK (tier IN ('instance', 'workspace')),
            key text NOT NULL,
            value jsonb NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (tier, key)
        )
    """)
    op.execute("""
        CREATE TABLE agent_instructions (
            repository_id uuid PRIMARY KEY REFERENCES repository (id) ON DELETE CASCADE,
            instructions text NOT NULL DEFAULT '',
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)


def downgrade() -> None:
    raise NotImplementedError
