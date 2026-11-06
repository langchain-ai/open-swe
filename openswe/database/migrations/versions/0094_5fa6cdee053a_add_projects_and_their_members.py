"""Add projects and their members"""

from alembic import op

revision = "5fa6cdee053a"
down_revision = ["c4f9b8528cee", "51127aaa9d2e", "53b42abdbed7"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A repository belongs to at most one project, so its threads file under one folder
    # for everyone in it.
    op.execute("""
        CREATE TABLE project (
            id uuid PRIMARY KEY,
            name text NOT NULL CHECK (btrim(name) <> ''),
            workspace_id uuid REFERENCES workspace(id) ON DELETE SET NULL,
            repository_id uuid UNIQUE REFERENCES repository(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    op.execute("""
        CREATE TABLE project_member (
            id uuid PRIMARY KEY,
            project_id uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role text NOT NULL CHECK (role IN ('owner', 'member')),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX project_member_project_user ON project_member(project_id, user_id)"
    )
    op.execute("CREATE INDEX project_member_user ON project_member(user_id)")


def downgrade() -> None:
    raise NotImplementedError
