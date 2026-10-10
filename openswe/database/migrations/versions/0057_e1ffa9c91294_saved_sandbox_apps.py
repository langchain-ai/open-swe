"""Saved sandbox apps: a person's named web apps, each served from a port of a thread's sandbox."""

from alembic import op

revision = "e1ffa9c91294"
down_revision = ["9ec28aa0b2a8", "b438344a06b7"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE sandbox_app (
            id uuid PRIMARY KEY,
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            name text NOT NULL,
            description text NOT NULL DEFAULT '',
            thread_id text NOT NULL,
            sandbox_id text NOT NULL,
            port integer NOT NULL,
            start_command text NOT NULL,
            workdir text NOT NULL,
            url text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (user_id, name)
        )
    """)


def downgrade() -> None:
    raise NotImplementedError
