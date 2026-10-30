"""Keep memories and each workspace's curated prompt.

A thread's ``remember`` keeps a memory: a personal one applies to the person who
saved it at once, and every memory waits for the workspace's curator, which may
fold what many threads learned into the workspace's prompt. Each curated prompt
is kept as a new version; a thread renders the latest when it opens.
"""

from alembic import op

revision = "e0605414666c"
down_revision = "0497c835bcfc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""
        CREATE TABLE memory (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL,
            scope text NOT NULL CHECK (scope IN ('personal', 'workspace')),
            workspace_id uuid REFERENCES workspace(id) ON DELETE CASCADE,
            thread_id text,
            from_private_thread boolean NOT NULL,
            body text NOT NULL,
            status text NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'held', 'adopted', 'declined')),
            decision_note text NOT NULL DEFAULT '',
            withdrawal_requested boolean NOT NULL DEFAULT false
        )
    """)
    op.execute(
        "CREATE INDEX memory_workspace_status ON memory (workspace_id, status, created_at) "
        "WHERE workspace_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX memory_personal ON memory (created_by, created_at) WHERE scope = 'personal'"
    )
    op.execute(
        "COMMENT ON TABLE memory IS 'What a thread remembered. created_by is the person whose "
        "run saved it, and a personal memory applies to that person''s runs.'"
    )
    op.execute(
        "COMMENT ON COLUMN memory.thread_id IS 'The MDA thread that saved it; threads live "
        "outside this database.'"
    )
    op.execute("COMMENT ON COLUMN memory.from_private_thread IS 'Saved in a private thread.'")
    op.execute(
        "COMMENT ON COLUMN memory.status IS 'The curator''s decision: pending until it decides; "
        "held when saved in a private thread its person did not share, so the curator never "
        "reads or decides it.'"
    )
    op.execute(
        "COMMENT ON COLUMN memory.withdrawal_requested IS 'Its person asked to take a "
        "workspace memory back; it waits for the curator again, which decides whether what "
        "it said leaves the prompt.'"
    )
    op.execute("""
        CREATE TABLE workspace_prompt_version (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL,
            workspace_id uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            prompt text NOT NULL
        )
    """)
    op.execute(
        "CREATE INDEX workspace_prompt_version_latest "
        "ON workspace_prompt_version (workspace_id, created_at DESC)"
    )
    op.execute(
        "COMMENT ON TABLE workspace_prompt_version IS 'Each prompt the workspace''s curator "
        "published; a thread renders the latest when it opens and keeps it for its life.'"
    )


def downgrade() -> None:
    raise NotImplementedError
