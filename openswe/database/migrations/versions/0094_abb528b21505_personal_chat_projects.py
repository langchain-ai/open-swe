"""Personal chat projects"""

from alembic import op

revision = "abb528b21505"
down_revision = ["c4f9b8528cee", "53b42abdbed7"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""
        CREATE TABLE chat_project (
            id uuid PRIMARY KEY,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
            repo text,
            local_path text,
            device_id text,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL,
            UNIQUE (id, user_id),
            CHECK ((local_path IS NULL) = (device_id IS NULL)),
            CHECK (repo IS NULL OR local_path IS NULL)
        )
    """)
    op.execute("CREATE INDEX chat_project_owner ON chat_project(user_id, created_at, id)")
    op.execute("CREATE INDEX chat_project_created_by ON chat_project(created_by)")
    op.execute("CREATE INDEX chat_project_updated_by ON chat_project(updated_by)")
    op.execute("""
        CREATE TABLE chat_project_membership (
            id uuid PRIMARY KEY,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            thread_id text NOT NULL REFERENCES thread_index(thread_id) ON DELETE CASCADE,
            project_id uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL,
            UNIQUE (user_id, thread_id),
            FOREIGN KEY (project_id, user_id) REFERENCES chat_project(id, user_id) ON DELETE CASCADE
        )
    """)
    op.execute(
        "CREATE INDEX chat_project_membership_project ON chat_project_membership(project_id)"
    )
    op.execute("CREATE INDEX chat_project_membership_thread ON chat_project_membership(thread_id)")
    op.execute(
        "CREATE INDEX chat_project_membership_created_by ON chat_project_membership(created_by)"
    )
    op.execute(
        "CREATE INDEX chat_project_membership_updated_by ON chat_project_membership(updated_by)"
    )


def downgrade() -> None:
    raise NotImplementedError
