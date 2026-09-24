"""Third-party GitHub bots an admin allows to prompt Open SWE from PR comments."""

from alembic import op

revision = "a04475f2cda5"
down_revision = "1e04f9eabcb0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE allowed_github_bot (
            github_id bigint PRIMARY KEY,
            login text NOT NULL,
            avatar_url text NOT NULL DEFAULT '',
            created_by text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX allowed_github_bot_login_idx ON allowed_github_bot (lower(login))"
    )


def downgrade() -> None:
    raise NotImplementedError
