"""Remember each user's repository frequency and recency."""

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE user_repository_usage (
            login text NOT NULL,
            repo text NOT NULL,
            use_count bigint NOT NULL DEFAULT 1 CHECK (use_count > 0),
            last_used_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (login, repo)
        )
    """)


def downgrade() -> None:
    raise NotImplementedError
