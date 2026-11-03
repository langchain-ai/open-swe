"""Persist suggested tasks"""

from alembic import op

revision = "981024b5de47"
down_revision = "4cbde59060e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE suggested_task (
            id uuid PRIMARY KEY,
            summary text NOT NULL,
            details text NOT NULL,
            workspace_id uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            repository_id uuid NOT NULL REFERENCES repository(id) ON DELETE CASCADE,
            thread_id text REFERENCES thread_index(thread_id) ON DELETE SET NULL,
            task_id uuid REFERENCES task(id) ON DELETE SET NULL,
            pull_request_id uuid REFERENCES pull_request(id) ON DELETE SET NULL,
            triggered_by uuid REFERENCES users(id) ON DELETE SET NULL,
            context_links jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    op.execute("CREATE INDEX suggested_task_target ON suggested_task(workspace_id, repository_id)")
    op.execute("CREATE INDEX suggested_task_thread ON suggested_task(thread_id)")
    op.execute("CREATE INDEX suggested_task_task ON suggested_task(task_id)")
    op.execute("CREATE INDEX suggested_task_pull_request ON suggested_task(pull_request_id)")
    op.execute("CREATE INDEX suggested_task_triggered_by ON suggested_task(triggered_by)")
    op.execute("CREATE INDEX suggested_task_created_by ON suggested_task(created_by)")
    op.execute("CREATE INDEX suggested_task_updated_by ON suggested_task(updated_by)")
    op.execute("""
        COMMENT ON TABLE suggested_task IS
            'Future work, filed under a target workspace and repository independently of its origin.'
    """)
    op.execute("""
        COMMENT ON COLUMN suggested_task.context_links IS
            'Additional source URLs, such as traces, issues, or code locations.'
    """)
    op.execute("""
        COMMENT ON COLUMN suggested_task.triggered_by IS
            'The person who triggered the originating run, including automated reviewer runs.'
    """)


def downgrade() -> None:
    raise NotImplementedError
