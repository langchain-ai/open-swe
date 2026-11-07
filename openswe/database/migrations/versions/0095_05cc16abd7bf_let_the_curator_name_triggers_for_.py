"""Let the curator name triggers for memories.

A memory linked to a trigger leaves its person's context: the run reads only the trigger's
summary of when it applies, and loads the memories behind it with ``recall(slug)``.
"""

from alembic import op

revision = "05cc16abd7bf"
down_revision = "5fa6cdee053a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""
        CREATE TABLE memory_trigger (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL,
            workspace_id uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            slug text NOT NULL,
            summary text NOT NULL
        )
    """)
    op.execute("CREATE UNIQUE INDEX memory_trigger_slug ON memory_trigger (workspace_id, slug)")
    op.execute(
        "COMMENT ON TABLE memory_trigger IS 'A trigger the workspace''s curator named: memories "
        "linked to it leave their person''s context, which shows only its summary of when they "
        "apply, and recall(slug) loads them.'"
    )
    op.execute("""
        CREATE TABLE memory_trigger_link (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL,
            trigger_id uuid NOT NULL REFERENCES memory_trigger(id) ON DELETE CASCADE,
            memory_id uuid NOT NULL REFERENCES memory(id) ON DELETE CASCADE
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX memory_trigger_link_pair "
        "ON memory_trigger_link (trigger_id, memory_id)"
    )
    op.execute("CREATE INDEX memory_trigger_link_memory ON memory_trigger_link (memory_id)")
    op.execute(
        "COMMENT ON TABLE memory_trigger_link IS 'Which memories a trigger recalls; a memory "
        "may have several triggers, and a trigger several memories.'"
    )


def downgrade() -> None:
    raise NotImplementedError
