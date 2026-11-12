"""Meter automation daily token usage"""

from alembic import op

revision = "c61dab93704d"
down_revision = ["cff3e5d43f43", "147a16ec2b4a", "c04ec5f250f1"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("CREATE INDEX automation_creator ON automation(created_by_user_id)")
    op.execute("""
        CREATE TABLE automation_token_usage (
            id uuid PRIMARY KEY,
            automation_id uuid NOT NULL REFERENCES automation(id) ON DELETE CASCADE,
            message_id text NOT NULL,
            tokens bigint NOT NULL CHECK (tokens >= 0),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL,
            UNIQUE (automation_id, message_id)
        )
    """)
    op.execute(
        "CREATE INDEX automation_token_day ON automation_token_usage(automation_id, created_at)"
    )


def downgrade() -> None:
    raise NotImplementedError
