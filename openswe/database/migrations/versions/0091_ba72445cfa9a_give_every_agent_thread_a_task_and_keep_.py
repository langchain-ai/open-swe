"""Give every agent thread a task and keep its goal there.

A task is the work a person hands over: one coordinating thread, and any workers it
delegates to. Every agent thread is its own task's coordinator from its first run on;
this backfills the threads indexed before, in their workspace or, like delegation, the
default one. Reviewer, scout, review chat and incident threads are the system's, not
handed-over work, and keep none.

Goal mode keeps where a task's work stands on the task: its state, why it waits or
paused, what it asked its person for, what will wake it, and who last moved it.
"""

from alembic import op

revision = "ba72445cfa9a"
down_revision = ["1850da6129ee", "3c9e71d2a4b8"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE task
            ADD COLUMN goal_state text,
            ADD COLUMN goal_reason text,
            ADD COLUMN goal_ask text,
            ADD COLUMN goal_waits jsonb,
            ADD COLUMN goal_updated_at timestamptz,
            ADD COLUMN goal_updated_by uuid REFERENCES users (id) ON DELETE SET NULL
    """)
    op.execute("""
        CREATE TEMPORARY TABLE untasked ON COMMIT DROP AS
        SELECT
            gen_random_uuid() AS task_id,
            thread.thread_id,
            coalesce(nullif(btrim(thread.metadata->>'title'), ''), 'Untitled') AS title,
            workspace.id AS workspace_id
        FROM thread_index AS thread
        JOIN workspace ON workspace.slug = coalesce(
            nullif(btrim(thread.metadata->>'workspace'), ''),
            nullif(btrim(thread.metadata->>'environment'), ''),
            'default'
        )
        WHERE coalesce(thread.metadata->>'graph_id', 'agent') = 'agent'
            AND thread.metadata->>'kind' IS NULL
            AND coalesce(thread.metadata->>'source', '') <> 'incidents_agent'
            AND NOT EXISTS (
                SELECT 1 FROM task_membership AS membership
                WHERE membership.thread_id = thread.thread_id
            )
    """)
    op.execute("""
        INSERT INTO task (id, coordinator_thread_id, title, workspace_id)
        SELECT task_id, thread_id, title, workspace_id FROM untasked
    """)
    op.execute("""
        INSERT INTO task_membership (thread_id, task_id, role)
        SELECT thread_id, task_id, 'coordinator' FROM untasked
    """)


def downgrade() -> None:
    raise NotImplementedError
