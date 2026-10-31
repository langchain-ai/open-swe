"""Add short links.

A short link redirects a short Open SWE URL to a stored target URL, so the agent
can hand people a URL it copies reliably in place of a long signed one.
"""

from alembic import op

revision = "c664a63ef3bc"
down_revision = "e0605414666c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""
        CREATE TABLE short_link (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL,
            token text NOT NULL,
            target_url text NOT NULL,
            expires_at timestamptz
        )
    """)
    op.execute("CREATE UNIQUE INDEX short_link_token ON short_link (token)")
    op.execute(
        "COMMENT ON TABLE short_link IS 'A short URL that redirects to target_url; anyone "
        "holding its token can follow it until expires_at. created_by is the person whose "
        "action made it.'"
    )
    op.execute("COMMENT ON COLUMN short_link.token IS 'The random secret in the link''s URL.'")
    op.execute(
        "COMMENT ON COLUMN short_link.target_url IS 'Where the link redirects; it may itself "
        "be a bearer URL, such as a signed download URL.'"
    )


def downgrade() -> None:
    raise NotImplementedError
