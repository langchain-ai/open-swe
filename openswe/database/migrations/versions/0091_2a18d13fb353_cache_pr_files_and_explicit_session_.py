"""Cache PR files and explicit session edits"""

from alembic import op

revision = "2a18d13fb353"
down_revision = "3c9e71d2a4b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE pull_request_file_cache (
            id uuid PRIMARY KEY,
            pull_request_id uuid NOT NULL UNIQUE REFERENCES pull_request(id) ON DELETE CASCADE,
            head_sha text NOT NULL,
            paths jsonb NOT NULL,
            active boolean NOT NULL,
            source_updated_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    op.execute("""
        CREATE INDEX pull_request_file_cache_paths ON pull_request_file_cache USING gin(paths)
    """)
    op.execute("""
        CREATE TABLE session_file_edit (
            id uuid PRIMARY KEY,
            repository_id uuid NOT NULL REFERENCES repository(id) ON DELETE CASCADE,
            thread_id text NOT NULL REFERENCES thread_index(thread_id) ON DELETE CASCADE,
            path text NOT NULL,
            branch text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL,
            UNIQUE (repository_id, thread_id, path)
        )
    """)
    op.execute("""
        CREATE INDEX session_file_edit_overlap ON session_file_edit(repository_id, path, updated_at);
    """)
    op.execute("""
        CREATE INDEX session_file_edit_expiry ON session_file_edit(updated_at)
    """)


def downgrade() -> None:
    raise NotImplementedError
