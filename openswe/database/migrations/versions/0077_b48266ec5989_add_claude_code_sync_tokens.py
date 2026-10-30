"""Add Claude Code sync tokens.

A person's Claude Code hooks present one of these to mirror their sessions into
Open SWE. Only the token's digest is kept, and revoking one deletes its row.
"""

from alembic import op

revision = "b48266ec5989"
down_revision = "79a94ddd5d16"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""
        CREATE TABLE claude_code_sync_token (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name text NOT NULL CHECK (name <> ''),
            token_hash text NOT NULL,
            token_suffix text NOT NULL,
            last_used_at timestamptz
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX claude_code_sync_token_hash ON claude_code_sync_token (token_hash)"
    )
    op.execute(
        "CREATE INDEX claude_code_sync_token_user ON claude_code_sync_token (user_id, created_at)"
    )


def downgrade() -> None:
    raise NotImplementedError
