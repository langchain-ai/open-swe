"""Add frontend review evidence"""

from alembic import op

revision = "311081ca2899"
down_revision = "6416028c3ce3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    op.execute("""
        CREATE TABLE frontend_review_run (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL,
            pull_request_id uuid NOT NULL REFERENCES pull_request(id) ON DELETE CASCADE,
            base_sha text NOT NULL CHECK (base_sha ~ '^[0-9a-fA-F]{40,64}$'),
            head_sha text NOT NULL CHECK (head_sha ~ '^[0-9a-fA-F]{40,64}$'),
            base_url text NOT NULL,
            head_url text NOT NULL,
            paths jsonb NOT NULL CHECK (jsonb_typeof(paths) = 'array'),
            auth_mode text NOT NULL CHECK (auth_mode IN ('dummy', 'none')),
            status text NOT NULL CHECK (status IN ('pending', 'running', 'complete', 'blocked', 'failed')),
            error text,
            report jsonb CHECK (jsonb_typeof(report) = 'object'),
            CHECK (auth_mode <> 'dummy' OR status = 'blocked'),
            CHECK (status <> 'complete' OR report IS NOT NULL)
        )
    """)
    op.execute("""
        CREATE INDEX frontend_review_run_pr_created_idx
        ON frontend_review_run(pull_request_id, created_at DESC)
    """)
    op.execute("""
        CREATE TABLE frontend_review_media (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL,
            run_id uuid NOT NULL REFERENCES frontend_review_run(id) ON DELETE CASCADE,
            name text NOT NULL CHECK (name ~ '^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,119}$'),
            content_type text NOT NULL CHECK (content_type IN ('image/png', 'video/webm', 'video/mp4')),
            content bytea NOT NULL CHECK (octet_length(content) BETWEEN 1 AND 8388608),
            UNIQUE (run_id, name)
        )
    """)


def downgrade() -> None:
    raise NotImplementedError
