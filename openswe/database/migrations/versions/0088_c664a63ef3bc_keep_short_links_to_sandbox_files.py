"""Keep short links to sandbox files.

A link names the file it opens, not a signed URL: each visit asks LangSmith for a
fresh one, so the agent hands people a short Open SWE URL instead of a long token.
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
            id text PRIMARY KEY,
            thread_id text NOT NULL REFERENCES thread (thread_id) ON DELETE CASCADE,
            sandbox_id text NOT NULL,
            path text NOT NULL,
            content_type text,
            content_disposition text NOT NULL
                CHECK (content_disposition IN ('attachment', 'inline')),
            expires_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("CREATE INDEX sandbox_file_link_thread ON sandbox_file_link (thread_id)")
    op.execute(
        "COMMENT ON TABLE sandbox_file_link IS 'A short link to one sandbox file; anyone "
        "holding its id can open the file until expires_at.'"
    )
    op.execute(
        "COMMENT ON COLUMN sandbox_file_link.path IS 'The resolved absolute path in the sandbox.'"
    )


def downgrade() -> None:
    raise NotImplementedError
