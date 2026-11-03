"""Automation ownership and daily token usage"""

from alembic import op

revision = "298986b248b4"
down_revision = ["c4f9b8528cee", "51127aaa9d2e", "53b42abdbed7"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "ALTER TABLE automation ADD COLUMN owner_id uuid REFERENCES users(id) ON DELETE SET NULL"
    )
    op.execute("""
        UPDATE automation a SET owner_id = i.user_id FROM user_identity i
        WHERE i.provider = 'github' AND lower(i.login) = lower(a.created_by)
    """)
    op.execute("CREATE INDEX automation_owner ON automation(owner_id)")
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
