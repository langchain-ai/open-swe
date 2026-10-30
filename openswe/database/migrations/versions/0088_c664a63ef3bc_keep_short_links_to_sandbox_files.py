"""Keep short links to sandbox files.

A link names the file it opens, not a signed URL: each visit asks LangSmith for a
fresh one, so the agent hands people a short Open SWE URL instead of a long signed one.
"""

from alembic import op

revision = "c664a63ef3bc"
down_revision = "e0605414666c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""
        CREATE TABLE sandbox_file_link (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL,
            token text NOT NULL,
            thread_id text NOT NULL REFERENCES thread (thread_id) ON DELETE CASCADE,
            sandbox_id text NOT NULL,
            path text NOT NULL,
            content_type text,
            content_disposition text NOT NULL
                CHECK (content_disposition IN ('attachment', 'inline')),
            expires_at timestamptz
        )
    """)
    op.execute("CREATE UNIQUE INDEX sandbox_file_link_token ON sandbox_file_link (token)")
    op.execute("CREATE INDEX sandbox_file_link_thread ON sandbox_file_link (thread_id)")
    op.execute(
        "COMMENT ON TABLE sandbox_file_link IS 'A short link to one sandbox file; anyone "
        "holding its token can open the file until expires_at. created_by is the person "
        "whose run made it.'"
    )
    op.execute(
        "COMMENT ON COLUMN sandbox_file_link.token IS 'The random secret in the link''s URL.'"
    )
    op.execute(
        "COMMENT ON COLUMN sandbox_file_link.path IS 'The resolved absolute path in the sandbox.'"
    )


def downgrade() -> None:
    raise NotImplementedError
