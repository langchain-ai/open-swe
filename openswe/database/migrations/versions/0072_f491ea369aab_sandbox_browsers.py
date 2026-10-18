"""Sandbox browsers: the LangSmith browser each person and each workspace lends its agents.

A row belongs to exactly one person or one workspace. ``langsmith_browser`` is
the browser sandbox's name in LangSmith, which keeps it, and the logins its
profile holds, until someone deletes it there.
"""

from alembic import op

revision = "f491ea369aab"
down_revision = "8272dd05ca91"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE sandbox_browser (
            id uuid PRIMARY KEY,
            user_id uuid REFERENCES users (id) ON DELETE CASCADE,
            workspace_id uuid REFERENCES workspace (id) ON DELETE CASCADE,
            langsmith_browser text NOT NULL,
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            CHECK (num_nonnulls(user_id, workspace_id) = 1)
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX sandbox_browser_user ON sandbox_browser (user_id) "
        "WHERE user_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX sandbox_browser_workspace ON sandbox_browser (workspace_id) "
        "WHERE workspace_id IS NOT NULL"
    )


def downgrade() -> None:
    raise NotImplementedError
