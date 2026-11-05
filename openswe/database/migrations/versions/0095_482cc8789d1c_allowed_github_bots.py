"""Allowed GitHub bots.

``allowed_github_bot`` holds one row per GitHub App bot login whose pull
requests Robin treats like a Robin user's.
"""

from alembic import op

revision = "482cc8789d1c"
down_revision = "5fa6cdee053a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE allowed_github_bot (
            id uuid PRIMARY KEY,
            login text NOT NULL,
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("CREATE UNIQUE INDEX allowed_github_bot_login ON allowed_github_bot (login)")


def downgrade() -> None:
    raise NotImplementedError
